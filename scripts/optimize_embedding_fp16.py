"""Prepare a validated half-precision copy of the local embedding model.

The workbench never downloads model weights here; it converts the weights that
are already cached for the configured embedding model and writes a separate
``<模型缓存>/fp16/<model>/`` directory. ``backend.knowledge.models`` loads that
directory automatically (``SHIXUE_EMBEDDING_FP16=0`` disables it), while both
copies keep the same vector-model identity, so existing vector indexes stay
searchable.

Half precision roughly halves GPU memory and runs the same encoder on the
tensor cores (measured ~1.3–1.8x on the local RTX 4070 Ti). The conversion is
validated on real-length sample sentences: the script refuses to publish a copy
whose cosine similarity to the fp32 model is below the configured threshold.

No ``onnx`` package is required: the protobuf wire format is patched directly,
and every field that is not converted is preserved byte for byte.

Usage (stop the workbench backend first so the GPU is free)::

    npm run setup:fp16
    # or: .venv\\Scripts\\python.exe scripts/optimize_embedding_fp16.py --validate
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Minimal ONNX protobuf reader/writer (onnx/onnx.proto subset).
# --------------------------------------------------------------------------- #
FLOAT, UINT8, INT8, UINT16, INT16, INT32, INT64 = 1, 2, 3, 4, 5, 6, 7
STRING, BOOL, FLOAT16, DOUBLE, UINT32, UINT64 = 8, 9, 10, 11, 12, 13
WT_VARINT, WT_64, WT_LEN, WT_32 = 0, 1, 2, 5
FP16_MAX = 65504.0
ALIGN = 64
SIDECARS = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")
# Weights with fp16 tensor-core support that were validated with this converter.
VALIDATED_MODELS = {"intfloat/multilingual-e5-large"}


def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def write_varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


class Field:
    """One protobuf field occurrence: tag + payload."""

    __slots__ = ("num", "wt", "val")

    def __init__(self, num: int, wt: int, val):
        self.num, self.wt, self.val = num, wt, val

    @classmethod
    def varint(cls, num: int, value: int) -> "Field":
        return cls(num, WT_VARINT, value)

    @classmethod
    def bytes(cls, num: int, value: bytes) -> "Field":
        return cls(num, WT_LEN, value)

    def serialize(self) -> bytes:
        tag = write_varint((self.num << 3) | self.wt)
        if self.wt == WT_VARINT:
            return tag + write_varint(self.val)
        if self.wt == WT_LEN:
            return tag + write_varint(len(self.val)) + self.val
        return tag + self.val


def parse(buf: bytes) -> list[Field]:
    fields: list[Field] = []
    i, size = 0, len(buf)
    while i < size:
        key, i = read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == WT_VARINT:
            value, i = read_varint(buf, i)
            fields.append(Field(num, wt, value))
        elif wt == WT_64:
            fields.append(Field(num, wt, buf[i:i + 8]))
            i += 8
        elif wt == WT_LEN:
            length, i = read_varint(buf, i)
            fields.append(Field(num, wt, buf[i:i + length]))
            i += length
        elif wt == WT_32:
            fields.append(Field(num, wt, buf[i:i + 4]))
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wt} for field {num}")
    return fields


def serialize(fields: list[Field]) -> bytes:
    return b"".join(field.serialize() for field in fields)


def get_all(fields: list[Field], num: int) -> list[Field]:
    return [field for field in fields if field.num == num]


def get_one(fields: list[Field], num: int) -> Field | None:
    for field in fields:
        if field.num == num:
            return field
    return None


def replace_all(fields: list[Field], num: int, new: list[Field]) -> None:
    out: list[Field] = []
    inserted = False
    for field in fields:
        if field.num == num:
            if not inserted:
                out.extend(new)
                inserted = True
            continue
        out.append(field)
    if not inserted:
        out.extend(new)
    fields[:] = out


# --------------------------------------------------------------------------- #
# fp16 conversion
# --------------------------------------------------------------------------- #
def to_fp16(array, np):
    """float32 -> float16, clamped to the finite fp16 range.

    The attention mask is built from ``-3.4028235e38``; in fp16 that constant
    becomes -inf and ``0 * -inf`` would poison the embedding with NaN. Clamping
    to +-65504 keeps the masking semantics exactly (softmax of -65504 is 0).
    """
    return np.clip(array.astype(np.float32), -FP16_MAX, FP16_MAX).astype(np.float16)


class ExternalWriter:
    def __init__(self, path: Path):
        self.fh = open(path, "wb")
        self.offset = 0

    def write(self, data: bytes) -> tuple[int, int]:
        padding = (-self.offset) % ALIGN
        if padding:
            self.fh.write(b"\x00" * padding)
            self.offset += padding
        start = self.offset
        self.fh.write(data)
        self.offset += len(data)
        return start, len(data)

    def close(self) -> None:
        self.fh.close()


def parse_external(tensor_fields: list[Field]) -> dict:
    value = {}
    for field in tensor_fields:
        entry = parse(field.val)
        key, item = get_one(entry, 1), get_one(entry, 2)
        if key and item:
            value[key.val.decode()] = item.val.decode()
    return value


def set_external(tensor_fields: list[Field], values: dict) -> None:
    entries = []
    for key, item in values.items():
        entry = [Field.bytes(1, key.encode()), Field.bytes(2, str(item).encode())]
        entries.append(Field.bytes(13, serialize(entry)))
    replace_all(tensor_fields, 13, entries)


def convert_tensor(np, tensor: list[Field], source, writer: ExternalWriter, data_name: str, stats: dict) -> None:
    dtype_field = get_one(tensor, 2)
    dtype = dtype_field.val if dtype_field else 0
    name_field = get_one(tensor, 8)
    name = name_field.val.decode() if name_field else "?"
    if dtype != FLOAT:
        return
    dims = []
    for dim in get_all(tensor, 1):
        dims.append(dim.val)
    count = 1
    for dim in dims:
        count *= dim
    external = get_all(tensor, 13)
    if external:
        values = parse_external(external)
        if values.get("location") != data_name:
            raise ValueError(f"{name}: unexpected external location {values.get('location')}")
        offset, length = int(values["offset"]), int(values["length"])
        if length != count * 4:
            raise ValueError(f"{name}: length {length} != {count} * 4 (dims={dims})")
        source.seek(offset)
        array = to_fp16(np.frombuffer(source.read(length), dtype=np.float32), np)
        new_offset, new_length = writer.write(array.tobytes())
        set_external(tensor, {"location": data_name, "offset": new_offset, "length": new_length})
        stats["converted_external"] += 1
        stats["bytes_in"] += length
        stats["bytes_out"] += new_length
        if dtype_field is not None:
            dtype_field.val = FLOAT16
        else:
            tensor.append(Field.varint(2, FLOAT16))
        return
    raw_field = get_one(tensor, 9)
    float_field = get_one(tensor, 4)
    if raw_field is not None:
        array = to_fp16(np.frombuffer(raw_field.val, dtype=np.float32)[:count], np)
    elif float_field is not None:
        array = to_fp16(np.frombuffer(float_field.val, dtype=np.float32)[:count], np)
    else:
        raise ValueError(f"{name}: float tensor with no data")
    half = array.tobytes()
    replace_all(tensor, 9, [Field.bytes(9, half)])
    replace_all(tensor, 4, [])
    if dtype_field is not None:
        dtype_field.val = FLOAT16
    else:
        tensor.append(Field.varint(2, FLOAT16))
    stats["converted_inline"] += 1
    stats["bytes_in"] += count * 4
    stats["bytes_out"] += len(half)


def patch_cast(tensor: list[Field]) -> int:
    patched = 0
    for attribute_field in get_all(tensor, 5):
        attribute = parse(attribute_field.val)
        name_field = get_one(attribute, 1)
        if not name_field or name_field.val.decode() != "to":
            continue
        target = get_one(attribute, 3)
        if target is not None and target.val == FLOAT:
            target.val = FLOAT16
            attribute_field.val = serialize(attribute)
            patched += 1
    return patched


def patch_outputs(graph: list[Field]) -> int:
    patched = 0
    for output_field in get_all(graph, 12):
        value_info = parse(output_field.val)
        type_field = get_one(value_info, 2)
        if type_field is None:
            continue
        type_proto = parse(type_field.val)
        tensor_type = get_one(type_proto, 1)
        if tensor_type is None:
            continue
        tensor = parse(tensor_type.val)
        element = get_one(tensor, 1)
        if element is not None and element.val == FLOAT:
            element.val = FLOAT16
            tensor_type.val = serialize(tensor)
            type_field.val = serialize(type_proto)
            output_field.val = serialize(value_info)
            patched += 1
    return patched


def convert_model(np, source_dir: Path, out_dir: Path, model_file: str, data_files: list[str]) -> dict:
    started = time.time()
    if len(data_files) > 1:
        raise SystemExit("当前转换器只处理单个外部权重文件：" + "、".join(data_files))
    source_model = source_dir / model_file
    data_name = data_files[0] if data_files else model_file + "_data"
    source_data = source_dir / data_name
    if not source_model.is_file():
        raise SystemExit(f"找不到模型文件：{source_model}")
    if data_files and not source_data.is_file():
        raise SystemExit(f"找不到外部权重文件：{source_data}")
    stats = {"converted_external": 0, "converted_inline": 0, "cast_patched": 0, "outputs_patched": 0,
             "bytes_in": 0, "bytes_out": 0}
    out_dir.mkdir(parents=True, exist_ok=True)
    writer = ExternalWriter(out_dir / data_name)
    top = parse(source_model.read_bytes())
    graph_field = get_one(top, 7)
    if graph_field is None:
        raise SystemExit("模型文件缺少计算图")
    graph = parse(graph_field.val)
    source = open(source_data, "rb") if data_files else None
    try:
        for field in get_all(graph, 5):
            tensor = parse(field.val)
            convert_tensor(np, tensor, source, writer, data_name, stats)
            field.val = serialize(tensor)
        for node_field in get_all(graph, 1):
            node = parse(node_field.val)
            op_field = get_one(node, 4)
            if op_field is not None and op_field.val.decode() == "Cast":
                stats["cast_patched"] += patch_cast(node)
            for attribute_field in get_all(node, 5):
                attribute = parse(attribute_field.val)
                tensor_field = get_one(attribute, 5)
                if tensor_field is not None:
                    tensor = parse(tensor_field.val)
                    convert_tensor(np, tensor, source, writer, data_name, stats)
                    tensor_field.val = serialize(tensor)
                    attribute_field.val = serialize(attribute)
            node_field.val = serialize(node)
    finally:
        if source is not None:
            source.close()
    stats["outputs_patched"] = patch_outputs(graph)
    graph_field.val = serialize(graph)
    (out_dir / model_file).write_bytes(serialize(top))
    writer.close()
    for name in SIDECARS:
        if (source_dir / name).is_file():
            shutil.copyfile(source_dir / name, out_dir / name)
    stats["source_model"] = str(source_model)
    stats["source_data"] = str(source_data) if data_files else ""
    stats["seconds"] = round(time.time() - started, 1)
    return stats


# --------------------------------------------------------------------------- #
# Validation: compare both copies through the workbench's own inference path.
# --------------------------------------------------------------------------- #
SAMPLE_SENTENCES = [
    "财政赋税制度在明代经历了从两税法到一条鞭法的转变，地方财政与中央财政的关系随之调整。",
    "《史记》记载，秦并天下，分天下以为三十六郡，郡置守、尉、监。",
    "先秦诸子对人性与礼法的讨论，构成中国古代政治思想的基本框架。",
    "清代乾嘉学派重视文字训诂与版本校勘，形成了一套严密的考证方法。",
    "凡治学之道，必先立其大者，而后可以及其小者；未有本乱而末治者也。",
    "The tax system changed substantially between the Ming and Qing dynasties.",
    "地方志中保存了大量赋役、户口与田亩数据，是研究区域社会经济的重要史料。",
    "学者每以版本源流为据，考订异文，辨析真伪，乃得其实。",
    "宋人笔记多记朝野见闻，虽未必尽实，然可补正史之阙。",
    "礼者，天地之序也；乐者，天地之和也。",
]
FILLER = "夫学者所以求益耳，见人读数十卷书便自高大，凌忽长者，轻慢同列；人疾之如仇敌，恶之如鸱枭。如此以学自损，不如无学也。"


def sample_texts():
    """Deterministic ~480-character samples, matching the production chunk length."""
    texts = []
    for index in range(64):
        base = SAMPLE_SENTENCES[index % len(SAMPLE_SENTENCES)]
        while len(base) < 460:
            base += "，".join([FILLER, SAMPLE_SENTENCES[(index + 3) % len(SAMPLE_SENTENCES)]])
        texts.append(base[:480])
    return texts


def load_local_model(model, cache, specific=None):
    from backend.knowledge.inference_runtime import options
    # Activate the GPU runtime before fastembed imports onnxruntime: an import
    # that wins this race pins the CPU-only wheel for the whole process.
    runtime, _requested = options()
    from fastembed import TextEmbedding
    names = [provider[0] if isinstance(provider, tuple) else provider for provider in runtime["providers"]]
    if "CUDAExecutionProvider" not in names:
        raise SystemExit("当前没有可用的 CUDA 推理运行时；fp16 仅在 GPU 上有效，请先完成 GPU 运行时安装")
    kwargs = {"model_name": model, "cache_dir": str(cache), "local_files_only": True, **runtime}
    if specific:
        kwargs["specific_model_path"] = str(specific)
    return TextEmbedding(**kwargs)


def cosine(values_a, values_b, np):
    a = np.asarray(values_a, dtype=np.float64)
    b = np.asarray(values_b, dtype=np.float64)
    norms = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
    return float(np.mean((a * b).sum(axis=1) / norms)), float(np.min((a * b).sum(axis=1) / norms))


def validate(model, cache, out_dir, np) -> dict:
    texts = [f"passage: {text}" for text in sample_texts()]
    started = time.time()
    baseline = list(load_local_model(model, cache).embed(texts, batch_size=16))
    baseline = [vector.tolist() for vector in baseline]
    gc.collect()
    converted = list(load_local_model(model, cache, out_dir).embed(texts, batch_size=16))
    converted = [vector.tolist() for vector in converted]
    mean, minimum = cosine(baseline, converted, np)
    return {"texts": len(texts), "cosine_mean": round(mean, 8), "cosine_min": round(minimum, 8),
            "seconds": round(time.time() - started, 1)}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def library_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    if os.environ.get("SHIXUE_KB_MODEL_CACHE"):
        return Path(os.environ["SHIXUE_KB_MODEL_CACHE"]).resolve().parent
    launcher = Path(".local/backend-launcher.json")
    if launcher.is_file():
        portal = Path(json.loads(launcher.read_text(encoding="utf-8"))["data_dir"])
        identity_file = portal / "private/library.json"
        if identity_file.is_file():
            identity = json.loads(identity_file.read_text(encoding="utf-8"))["workspace_id"]
            shared = portal / "private/runtimes" / identity / "shared-database.json"
            if shared.is_file():
                return Path(json.loads(shared.read_text(encoding="utf-8"))["root"]).resolve()
        library = portal / "shared-library"
        if library.is_dir():
            return library.resolve()
    raise SystemExit("无法定位文献库根目录；请用 --library-root 指定（其下应有 models/ 模型缓存目录）")


def model_description(model: str) -> dict:
    from fastembed import TextEmbedding
    for description in TextEmbedding.list_supported_models():
        if description["model"].lower() == model.lower():
            return description
    raise SystemExit(f"当前 fastembed 运行时不支持模型 {model}")


def source_directory(cache: Path, description: dict) -> tuple[Path, dict]:
    source = (description.get("sources") or {}).get("hf")
    if not source:
        raise SystemExit("该模型没有 HuggingFace 缓存来源，无法转换")
    repo = cache / ("models--" + source.replace("/", "--"))
    ref = repo / "refs/main"
    revision = ref.read_text(encoding="utf-8").strip() if ref.is_file() else ""
    candidates = [repo / "snapshots" / revision] if revision else []
    candidates += sorted(path for path in (repo / "snapshots").glob("*") if path.is_dir()) if (repo / "snapshots").is_dir() else []
    for folder in candidates:
        if (folder / description["model_file"]).is_file():
            return folder, description
    raise SystemExit(f"模型缓存不完整，请先在后台“向量模型”页准备并校验 {description['model']}")


def gpu_free_mib() -> int:
    try:
        output = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        return int(output[0]) if output else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="为本地嵌入模型准备经过校验的 fp16 加速副本")
    parser.add_argument("--model", default="intfloat/multilingual-e5-large")
    parser.add_argument("--library-root", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--validate", action="store_true", help="在 GPU 上对比 fp32 与 fp16 输出的余弦相似度")
    parser.add_argument("--force", action="store_true", help="覆盖已有的加速副本")
    parser.add_argument("--min-cosine", type=float, default=0.999)
    args = parser.parse_args()

    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # Activate the project GPU runtime *before* fastembed (and therefore
    # onnxruntime) is imported below. Otherwise the CPU-only wheel from .venv
    # wins the race and validation reports "没有可用的 CUDA 推理运行时".
    from backend.knowledge.inference_runtime import options as runtime_options
    runtime_options()
    from backend.knowledge.models import model_identity

    if args.model not in VALIDATED_MODELS:
        raise SystemExit(f"该模型尚未通过本转换器的数值校验，暂不支持：{args.model}")
    root = library_root(args.library_root)
    cache = Path(os.environ.get("SHIXUE_KB_MODEL_CACHE") or root / "models").resolve()
    description = model_description(args.model)
    source, _ = source_directory(cache, description)
    data_files = list(description.get("additional_files") or [])
    out_dir = Path(args.out).resolve() if args.out else cache / "fp16" / args.model.replace("/", "--")
    if out_dir.exists() and any(out_dir.iterdir()):
        meta_path = out_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        if meta.get("validated"):
            print(f"已存在经过校验的加速副本：{out_dir}（如需重建请加 --force）")
            return
        if not args.force and args.validate:
            if not (out_dir / "model.onnx").is_file():
                raise SystemExit(f"加速副本不完整：{out_dir}，请加 --force 重新转换")
            free_mib = gpu_free_mib()
            if free_mib and free_mib < 7000:
                raise SystemExit(f"GPU 空闲显存只有 {free_mib} MiB，校验需要约 7 GB。请先停止后台（npm stop）后重试")
            print(f"校验已有加速副本：{out_dir}")
            result = validate(args.model, cache, out_dir, np)
            print(f"  余弦相似度：mean={result['cosine_mean']} min={result['cosine_min']}")
            if result["cosine_mean"] < args.min_cosine or result["cosine_min"] < args.min_cosine - 0.004:
                shutil.rmtree(out_dir, ignore_errors=True)
                raise SystemExit("校验未通过，已删除加速副本；仍使用原始模型")
            meta.update(validated=True, validation=result, validated_at=time.time())
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            print("  校验通过；重启后台后自动使用 fp16 加速副本")
            return
        if not args.force:
            print(f"已存在未经校验的加速副本：{out_dir}（用 --validate 校验，或 --force 重建）")
            return

    free_mib = gpu_free_mib()
    if args.validate and free_mib and free_mib < 7000:
        raise SystemExit(f"GPU 空闲显存只有 {free_mib} MiB，校验需要约 7 GB。请先停止后台（npm stop）后重试")

    free_gb = shutil.disk_usage(str(cache)).free / 1024 ** 3
    needed_gb = (source / data_files[0]).stat().st_size / 1024 ** 3 * 0.6 if data_files else 0.5
    if free_gb < needed_gb + 0.5:
        raise SystemExit(f"磁盘空间不足：剩余 {free_gb:.1f} GB，需要约 {needed_gb:.1f} GB")

    staging = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    print(f"转换 {args.model}\n  源目录：{source}\n  输出目录：{out_dir}")
    stats = convert_model(np, source, staging, description["model_file"], data_files)
    (staging / "identity.txt").write_text(model_identity(args.model, source), encoding="utf-8")
    meta = {"model": args.model, "format": "onnx-fp16", "dimensions": description["dim"], "source": str(source),
            "created_at": time.time(), "conversion": stats, "validated": False}
    if args.validate:
        print("正在对比 fp32 与 fp16 的输出（需要约 1-2 分钟）…")
        result = validate(args.model, cache, staging, np)
        print(f"  余弦相似度：mean={result['cosine_mean']} min={result['cosine_min']}")
        if result["cosine_mean"] < args.min_cosine or result["cosine_min"] < args.min_cosine - 0.004:
            shutil.rmtree(staging, ignore_errors=True)
            raise SystemExit("校验未通过，已删除加速副本；仍使用原始模型")
        meta["validation"] = result
        meta["validated"] = True
    (staging / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(out_dir, ignore_errors=True)
    staging.rename(out_dir)
    print(f"完成：{out_dir}")
    print(f"  权重 {stats['bytes_in'] / 1024 ** 3:.2f} GB -> {stats['bytes_out'] / 1024 ** 3:.2f} GB，耗时 {stats['seconds']}s")
    if args.validate:
        print("  校验通过；重启后台后自动使用 fp16 加速副本")
    else:
        print("  未做数值校验，后台不会自动使用（请加 --validate 重新运行）")


if __name__ == "__main__":
    main()
