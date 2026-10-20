"""Build a verifiable Windows runtime from EDB binaries and pgvector source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET

PG_ARCHIVE_SHA256 = '6eabdf00d2893713b75db4336a23c3fdf505f056e217ec6e2e95d901750cfea3'
PG_SOURCE_URL = 'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64-binaries.zip'
VECTOR_SOURCE_URL = 'https://github.com/pgvector/pgvector/tree/v0.8.7'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--downloads', type=Path, default=Path('.local/runtime-downloads'))
    parser.add_argument('--destination', type=Path, default=Path('build/postgres'))
    args = parser.parse_args()
    downloads = args.downloads.resolve()
    destination = args.destination.resolve()
    archive = downloads / 'postgresql-17.11-1-windows-x64-binaries.zip'
    if digest(archive) != PG_ARCHIVE_SHA256:
        raise RuntimeError('PostgreSQL archive checksum mismatch')
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if name.endswith('/'):
                continue
            relative = name.removeprefix('pgsql/')
            # Headers/import libs enable the reproducible extension build; pgAdmin is unnecessary.
            if relative.split('/')[0] not in {'bin', 'lib', 'share', 'include'} and relative not in {
                'server_license.txt', 'commandlinetools_3rd_party_licenses.txt'}:
                continue
            output = (destination / relative).resolve()
            if destination not in output.parents:
                raise RuntimeError('Invalid runtime archive path')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(source.read(name))
    vc = downloads / 'msvc/VC/Tools/MSVC/14.44.35207'
    sdk = downloads / 'sdk/Windows Kits/10'
    environment = {**os.environ, 'PGROOT': str(destination),
                   'PATH': str(vc / 'bin/Hostx64/x64') + os.pathsep + os.environ['PATH'],
                   'INCLUDE': ';'.join(str(p) for p in (vc / 'include', sdk / 'Include/10.0.26100.0/ucrt',
                                                       sdk / 'Include/10.0.26100.0/shared', sdk / 'Include/10.0.26100.0/um')),
                   'LIB': ';'.join(str(p) for p in (vc / 'lib/x64', sdk / 'Lib/10.0.26100.0/ucrt/x64',
                                                   sdk / 'Lib/10.0.26100.0/um/x64'))}
    source = downloads / 'pgvector-0.8.7'
    for command in ([str(vc / 'bin/Hostx64/x64/nmake.exe'), '/F', 'Makefile.win'],
                    [str(vc / 'bin/Hostx64/x64/nmake.exe'), '/F', 'Makefile.win', 'install']):
        subprocess.run(command, cwd=source, env=environment, check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    shutil.copy2(source / 'LICENSE', destination / 'pgvector-LICENSE')
    vc_runtime=downloads/'vc-redist-runtime/System64'
    if not (vc_runtime/'vcruntime140.dll').exists():
        raise RuntimeError('Verified Microsoft x64 VC redistributable is required')
    for dll in vc_runtime.glob('*.dll'):
        shutil.copy2(dll,destination/'bin'/dll.name)
    shutil.copy2(downloads/'vc-redist-source/license.rtf',destination/'Microsoft-VC-license.rtf')
    # EDB's tools have no UTF-8 process manifest. Without it initdb sends CP936
    # directory literals to a UTF-8 backend and fails on Chinese paths.
    # Only the bundled copies are modified; archive/source hashes stay recorded.
    from PyInstaller.utils.win32 import winmanifest
    for executable in (destination / 'bin').glob('*.exe'):
        try:
            original = winmanifest.read_manifest_from_executable(str(executable))
        except ValueError:
            original = winmanifest.create_application_manifest()
        if isinstance(original, tuple):
            original = original[1]
        document = ET.fromstring(original)
        app = ET.SubElement(document, '{urn:schemas-microsoft-com:asm.v3}application')
        settings = ET.SubElement(app, '{urn:schemas-microsoft-com:asm.v3}windowsSettings')
        ET.SubElement(settings, '{http://schemas.microsoft.com/SMI/2019/WindowsSettings}activeCodePage').text = 'UTF-8'
        winmanifest.write_manifest_to_executable(str(executable), ET.tostring(document, encoding='utf-8', xml_declaration=True))
    # Include extension SQL/control, runtime DLLs and required license notices in the manifest.
    files = {p.relative_to(destination).as_posix(): digest(p) for p in destination.rglob('*')
             if p.is_file() and p.name != 'runtime-manifest.json'}
    manifest = {'postgres_version': '17.11', 'pgvector_version': '0.8.7', 'platform': 'windows-x64',
                'postgres_source': PG_SOURCE_URL, 'postgres_archive_sha256': PG_ARCHIVE_SHA256,
                'vector_source': VECTOR_SOURCE_URL,
                'vector_source_archive_sha256': digest(downloads / 'pgvector-v0.8.7.zip'),
                'compiler': 'MSVC 14.44.35229 / Windows SDK 10.0.26100.0',
                'build_inputs_sha256': digest(Path(__file__).resolve().parents[1]/'deployment/knowledge/managed-runtime-inputs.json'), 'files': files}
    manifest['vc_redistributable_sha256']=digest(downloads/'VC_redist.x64.exe')
    manifest['packaging_changes'] = ['UTF-8 activeCodePage manifest on bundled CLI executables']
    manifest['minimum_windows_build'] = 18362
    (destination / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Windows PostgreSQL runtime built and verified:', len(files), 'files')


if __name__ == '__main__':
    main()
