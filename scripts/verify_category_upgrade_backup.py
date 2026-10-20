"""Independently stream-verify every file in the actual pre-upgrade archive."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.local/categories-rc9'


def main():
    report=json.loads((OUT/'category-upgrade-report.json').read_text(encoding='utf-8'))
    if not report.get('backup'):raise RuntimeError('缺少本轮升级前完整备份路径')
    root=(ROOT/'工作台数据').resolve()
    backup=(root/report['backup']).resolve()
    if root not in backup.parents or not backup.is_file():raise RuntimeError('备份路径无效')
    verified=0;size=0
    with zipfile.ZipFile(backup) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        if manifest['schema_revision']!=report['from_schema']:raise RuntimeError('备份结构版本不一致')
        if not {'library.sqlite','knowledge.dump'}<=set(manifest['files']):raise RuntimeError('备份缺少数据库')
        names=archive.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(manifest['files'])|{'manifest.json'}:raise RuntimeError('备份文件清单不一致')
        for name,expected in manifest['files'].items():
            with archive.open(name) as source:
                actual=hashlib.file_digest(source,'sha256').hexdigest()
            if actual!=expected:raise RuntimeError('备份文件哈希不一致：'+name)
            verified+=1;size+=archive.getinfo(name).file_size
    result={'backup':str(backup),'schema_revision':manifest['schema_revision'],'all_files_verified':True,
            'files':verified,'uncompressed_bytes':size,'archive_bytes':backup.stat().st_size}
    (OUT/'category-backup-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
