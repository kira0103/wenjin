"""Publish verification evidence alongside the rc.7 candidate."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.5.0-rc.7'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    release = ROOT / 'release' / ('v' + VERSION)
    output = ROOT / '.local/submissions-20261018'
    resources = release / 'win-unpacked/resources'
    backend = resources / 'backend/ShixueWorkbenchService.exe'
    if sha(backend) != sha(ROOT / 'build/backend/ShixueWorkbenchService.exe'):
        raise RuntimeError('包内后端与最终构建不一致')
    gui_path = output / 'packaged-gui-report.json'
    gui = json.loads(gui_path.read_text(encoding='utf-8'))
    required = ('editable_fields_persisted', 'manual_edits_preserved_on_import', 'incremental_csv_ui',
                'rejection_note_preserved', 'work_result_association')
    if gui['version'] != VERSION or not all(gui[k] for k in required) or gui['final_status'] != 'published':
        raise RuntimeError('打包界面验收尚未通过')
    if gui_path.stat().st_mtime < max(backend.stat().st_mtime, (resources / 'app.asar').stat().st_mtime):
        raise RuntimeError('界面验收早于最终构建')
    data = json.loads((output / 'candidate-data-verification.json').read_text(encoding='utf-8'))
    if not data['ready'] or not data['source_unmodified'] or data['journals'] != 935 or data['books'] != 15295 or data['files'] != 28950:
        raise RuntimeError('现有资料复制验收尚未通过')
    tests = json.loads((output / 'full-result.json').read_text(encoding='utf-8'))
    if tests['failures'] or tests['errors'] or not tests['native']:
        raise RuntimeError('回归测试未通过')
    journal_tests = json.loads((output / 'journal-result.json').read_text(encoding='utf-8'))
    if journal_tests['tests'] != 7 or journal_tests['failures'] or journal_tests['errors']:
        raise RuntimeError('最终期刊功能测试未通过')
    runtime_path = output / 'candidate-runtime-report.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    if runtime['version'] != VERSION or not runtime['ready'] or not runtime['original_csv_already_imported'] or not runtime['core_original_search_hits']:
        raise RuntimeError('全量资料打包运行验收未通过')
    if runtime_path.stat().st_mtime < backend.stat().st_mtime:
        raise RuntimeError('全量资料运行报告早于最终后端')
    script = r'''
const fs=require('node:fs'),path=require('node:path'),asar=require('@electron/asar');
const archive=process.argv[1],dist=process.argv[2];
const pkg=JSON.parse(asar.extractFile(archive,'package.json'));if(pkg.version!=='0.5.0-rc.7')throw Error('Wrong version');
let checked=0;function verify(folder){for(const entry of fs.readdirSync(folder,{withFileTypes:true})){
const source=path.join(folder,entry.name);if(entry.isDirectory()){verify(source);continue;}
const relative=path.relative(dist,source);
if(!fs.readFileSync(source).equals(asar.extractFile(archive,path.join('dist',relative))))throw Error('Frontend mismatch: '+relative);checked++;
}}verify(dist);for(const name of ['main.cjs','preload.cjs'])if(!fs.readFileSync(path.join('electron',name)).equals(asar.extractFile(archive,path.join('electron',name))))throw Error('Electron mismatch');
process.stdout.write(JSON.stringify({version:pkg.version,frontend_files_verified:checked,electron_files_verified:2}));
'''
    result = subprocess.run(['node', '-e', script, str(resources / 'app.asar'), str(ROOT / 'dist')],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('包内界面核验失败：' + result.stderr[:1000])
    files = [release / f'史学工作台候选版 {VERSION}.exe', release / f'史学工作台候选版 Setup {VERSION}.exe']
    if any(file.stat().st_mtime < backend.stat().st_mtime for file in files):
        raise RuntimeError('发布程序早于最终后端，请先重新生成安装包或免安装版')
    checksums = {file.name: sha(file) for file in files}
    (release / 'SHA256.json').write_text(json.dumps(checksums, ensure_ascii=False, indent=2), encoding='utf-8')
    for original, destination in ((ROOT / f'论文投稿管理-{VERSION}-实施与使用.md', release / '使用与验收.md'),
                                  (gui_path, release / '投稿管理验收.json'),
                                  (runtime_path, release / '实际资料运行验收.json'),
                                  (output / 'candidate-data-verification.json', release / '资料复制验收.json')):
        shutil.copy2(original, destination)
    launcher = (ROOT / 'release/v0.5.0-rc.6/启动候选版.cmd').read_text(encoding='utf-8').replace('0.5.0-rc.6', VERSION)
    (release / '启动候选版.cmd').write_text(launcher, encoding='utf-8')
    report = {**json.loads(result.stdout), 'backend_matches_build': True, 'backend_sha256': sha(backend),
              'packaged_gui_verified': True, 'data_copy_verified': True, 'regression': tests,
              'journal_tests': journal_tests, 'actual_data_runtime': runtime, 'checksums': checksums}
    (output / 'candidate-seal.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'sealed': True, 'version': VERSION, 'journals': data['journals'], 'books_preserved': data['books']}))


if __name__ == '__main__':
    main()
