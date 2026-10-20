"""Fetch hash-pinned vendor build inputs without installing compiler/SDK globally."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import uuid
import xml.etree.ElementTree as ET
import zipfile

import httpx


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def bounded(root, name):
    output = (root / name).resolve()
    if root.resolve() not in output.parents:
        raise RuntimeError('Build input path escapes the selected directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def run(command):
    result = subprocess.run([str(value) for value in command], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=120)
    if result.returncode:
        raise RuntimeError('Build input extraction failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, default=Path('.local/runtime-downloads'))
    parser.add_argument('--sevenzip', type=Path, default=Path('node_modules/7zip-bin/win/x64/7za.exe'))
    args = parser.parse_args()
    root = args.destination.resolve()
    source = Path(__file__).resolve().parents[1] / 'deployment/knowledge/managed-runtime-inputs.json'
    inputs = json.loads(source.read_text(encoding='utf-8'))

    def download(item):
        target = bounded(root, item['path'])
        if target.exists() and digest(target).lower() == item['sha256'].lower():
            return
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.download')
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            with client.stream('GET', item['url']) as response:
                response.raise_for_status()
                with temporary.open('wb') as stream:
                    for block in response.iter_bytes(1024 * 1024):
                        stream.write(block)
        if digest(temporary).lower() != item['sha256'].lower():
            raise RuntimeError('Vendor input checksum mismatch')
        temporary.replace(target)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(download, inputs['artifacts']))
    for item in inputs['artifacts']:
        if item['kind'] != 'msvc':
            continue
        with zipfile.ZipFile(root / item['path']) as archive:
            for name in archive.namelist():
                if name.startswith('Contents/') and not name.endswith('/'):
                    output = bounded(root / 'msvc', name.removeprefix('Contents/'))
                    output.write_bytes(archive.read(name))
    with zipfile.ZipFile(root / 'pgvector-v0.8.7.zip') as archive:
        for name in archive.namelist():
            if not name.endswith('/'):
                bounded(root, name).write_bytes(archive.read(name))
    sdk = root / 'sdk'
    sdk.mkdir(exist_ok=True)
    for msi in (root / 'sdk-installers').glob('*.msi'):
        run(['msiexec.exe', '/a', msi, '/qn', 'TARGETDIR=' + str(sdk)])

    # Microsoft's Burn redistributable contains a bootstrap and attached CAB.
    # Extract its named x64 MSI payloads, never install it on the host.
    binary = (root / 'VC_redist.x64.exe').read_bytes()
    sevenzip = args.sevenzip.resolve()
    payloads = root / 'vc-redist-payloads'
    start, index = 0, 0
    while True:
        start = binary.find(b'MSCF', start)
        if start < 0:
            break
        size = struct.unpack_from('<I', binary, start + 8)[0]
        if size > 40 and start + size <= len(binary):
            cabinet = root / ('redist-container-' + str(index) + '.cab')
            cabinet.write_bytes(binary[start:start + size])
            run([sevenzip, 'x', cabinet, '-o' + str(payloads), '-y'])
            index += 1
        start += 4
    metadata = ET.fromstring((payloads / '0').read_bytes())
    for node in metadata.iter():
        if node.tag.split('}')[-1] == 'Payload':
            origin, filename = node.attrib.get('SourcePath'), node.attrib.get('FilePath')
            if origin and filename:
                bounded(root / 'vc-redist-source', filename.replace('\\', '/')).write_bytes(
                    bounded(payloads, origin).read_bytes())
    redist = root / 'vc-redist-runtime'
    redist.mkdir(exist_ok=True)
    for msi in (root / 'vc-redist-source/packages').rglob('*x64.msi'):
        run(['msiexec.exe', '/a', msi, '/qn', 'TARGETDIR=' + str(redist)])
    print('Vendor inputs verified and extracted:', len(inputs['artifacts']))


if __name__ == '__main__':
    main()
