# syntax=docker/dockerfile:1.7

# Build the React bundle once, then ship only static assets in the runtime image.
FROM node:22-bookworm-slim AS web
WORKDIR /src
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
COPY electron/preload.d.ts ./electron/preload.d.ts
RUN npm run build:ui

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SHIXUE_GPU_RUNTIME=/opt/gpu-runtime \
    SHIXUE_EMBEDDING_DEVICE=cuda \
    SHIXUE_INGEST_CONCURRENCY=4 \
    SHIXUE_EMBED_PREFETCH=4 \
    SHIXUE_EMBED_WORKERS=2 \
    SHIXUE_EMBED_INFLIGHT=2 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl libgl1 libglib2.0-0 libgomp1 tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --upgrade pip && pip install -r backend/requirements.txt

# Keep the CUDA runtime isolated, matching the Windows project's .local/gpu-runtime
# design instead of replacing the CPU onnxruntime wheel in the main environment.
COPY backend/requirements-gpu.txt backend/requirements-gpu.txt
RUN pip install --target /opt/gpu-runtime -r backend/requirements-gpu.txt \
    && printf '%s\n' '{"validated":true,"python":"3.12","model":"intfloat/multilingual-e5-large","providers":["CUDAExecutionProvider","CPUExecutionProvider"]}' > /opt/gpu-runtime/ready.json

# Libraries installed by nvidia-*-cu12 wheels live in several target subfolders.
ENV LD_LIBRARY_PATH=/opt/gpu-runtime/nvidia/cublas/lib:/opt/gpu-runtime/nvidia/cudnn/lib:/opt/gpu-runtime/nvidia/cuda_runtime/lib:/opt/gpu-runtime/nvidia/cufft/lib:/opt/gpu-runtime/nvidia/curand/lib:/opt/gpu-runtime/nvidia/nvjitlink/lib

COPY backend ./backend
COPY scripts ./scripts
COPY docker ./docker
COPY --from=web /src/dist ./dist
RUN chmod 0755 /app/docker/entrypoint.sh

EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
