"""Seal rc.10 only after packaged GUI and real fixed-workspace checks pass."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.5.0-rc.10'
OUT = ROOT / '.local/folders-rc10'
RELEASE = ROOT / 'release' / ('v' + VERSION)
DATA = ROOT / '工作台数据'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def require(value, message):
    if not value:
        raise RuntimeError(message)


def main():
    resources = RELEASE / 'win-unpacked/resources'
    backend = resources / 'backend/ShixueWorkbenchService.exe'
    require(sha(backend) == sha(ROOT / 'build/backend/ShixueWorkbenchService.exe'), '包内后端与最终构建不一致')
    gui = read('packaged-gui-report.json')
    runtime = read('fixed-runtime-report.json')
    bootstrap = read('bootstrap-gui-report.json')
    tests = read('full-result.json')
    upgrade = read('folder-upgrade-report.json')
    require(not tests['errors'] and not tests['failures'], '完整回归未通过')
    focused = (OUT / 'folder-tests.log').read_text(encoding='utf-8')
    require('Ran 5 tests' in focused and focused.rstrip().endswith('OK'), '最终目录迁移回归未通过')
    require(gui['version'] == VERSION and gui['schema'] == 'kb0012'
            and gui['library_tabs'] == ['资料目录', '统一检索'], '打包界面版本或入口不正确')
    require(all(gui[k] for k in (
        'multi_pdf_import', 'default_uncategorized', 'directory_structure',
        'file_scope_search', 'file_scope_research', 'group_scope_research',
        'manual_reclassification', 'core_reuse_no_duplicates', 'single_file_collation',
        'custom_skill_used', 'trial_did_not_activate', 'skill_version_rollback',
        'skill_export', 'custom_agent_bound', 'project_result_saved',
        'notes_not_created', 'storage_controls', 'question_handoff')), '目录或助手界面验收未通过')
    require(gui['team_outcome'] == 'complete' and len(gui['team_nodes']) == 4
            and all(n['state'] == 'succeeded' for n in gui['team_nodes'])
            and gui['cancelled_inflight_calls'] >= 2, '协同研究验收未通过')
    require(upgrade['to_schema'] == 'kb0012' and upgrade['existing_data_equal']
            and upgrade['before'] == upgrade['after'] and upgrade['backup_directory_unchanged']
            and not any(upgrade[k] for k in ('automatic_backup', 'originals_copied', 'indexes_rebuilt', 'version_data_copy')),
            '实际资料保留或不自动备份核验未通过')
    require(Path(upgrade['data_root']).resolve() == DATA.resolve(), '升级资料路径不正确')
    require(runtime['version'] == VERSION and runtime['ready'] and runtime['schema_revision'] == 'kb0012'
            and runtime['journals'] >= 935 and runtime['original_csv_already_imported']
            and runtime['original_csv_new_journals'] == 0 and runtime['core_original_search_hits']
            and runtime['unified_global_hits'] and runtime['single_file_search_verified']
            and runtime['document_files'] == upgrade['files'] and runtime['data_outside_release'],
            '最终程序的实际资料运行核验未通过')
    require(bootstrap['packaged_main_verified'] and bootstrap['stable_workspace']
            and len(bootstrap['launches']) == 2
            and all(Path(v['dataDir']).resolve() == DATA.resolve() and v['no_version_data_copy']
                    for v in bootstrap['launches']), '真实主程序固定目录启动核验未通过')
    for name in ('packaged-gui-report.json', 'fixed-runtime-report.json', 'bootstrap-gui-report.json'):
        require((OUT / name).stat().st_mtime >= max(backend.stat().st_mtime, (resources / 'app.asar').stat().st_mtime),
                '验收报告早于最终程序：' + name)
    require(not (DATA / 'knowledge-db/postmaster.pid').exists(), '资料库尚未正常停止')
    require(not any((RELEASE / name).exists() for name in ('工作台数据', '史学工作台验证数据')), '发布目录包含重复资料')
    config_file = Path(os.environ['APPDATA']) / 'shixue-workbench/bootstrap.json'
    config = json.loads(config_file.read_text(encoding='utf-8-sig'))
    require(Path(config['dataDir']).resolve() == DATA.resolve(), '现有启动配置没有指向固定资料目录')
    # Read the stable pointer; do not rewrite the user's desktop configuration.
    script = r'''
const fs=require('node:fs'),path=require('node:path'),asar=require('@electron/asar');
const archive=process.argv[1],dist=process.argv[2],version=process.argv[3];
if(JSON.parse(asar.extractFile(archive,'package.json')).version!==version)throw Error('Wrong version');
let checked=0;function walk(folder){for(const entry of fs.readdirSync(folder,{withFileTypes:true})){
const file=path.join(folder,entry.name);if(entry.isDirectory()){walk(file);continue;}
if(!fs.readFileSync(file).equals(asar.extractFile(archive,path.join('dist',path.relative(dist,file)))))throw Error('Frontend mismatch: '+file);checked++;
}}walk(dist);
for(const name of ['main.cjs','preload.cjs','workspace-path.cjs'])
if(!fs.readFileSync(path.join('electron',name)).equals(asar.extractFile(archive,path.join('electron',name))))throw Error('Electron mismatch: '+name);
process.stdout.write(JSON.stringify({frontend_files_verified:checked,electron_files_verified:3}));
'''
    checked = subprocess.run(['node', '-e', script, str(resources / 'app.asar'), str(ROOT / 'dist'), VERSION],
                             cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True)
    executables = [RELEASE / f'史学工作台候选版 {VERSION}.exe', RELEASE / f'史学工作台候选版 Setup {VERSION}.exe']
    require(all(p.stat().st_mtime >= backend.stat().st_mtime for p in executables), '发布文件早于最终后端')
    checksums = {p.name: sha(p) for p in executables}
    (RELEASE / 'SHA256.json').write_text(json.dumps(checksums, ensure_ascii=False, indent=2), encoding='utf-8')
    for source, destination in (
        (ROOT / f'文献目录导入-{VERSION}-实施与使用.md', '使用与验收.md'),
        (OUT / 'packaged-gui-report.json', '文献库界面验收.json'),
        (OUT / 'bootstrap-gui-report.json', '固定目录启动验收.json'),
        (OUT / 'fixed-runtime-report.json', '实际资料运行验收.json'),
        (OUT / 'folder-upgrade-report.json', '目录升级与数据保留验收.json'),
        (OUT / 'full-result.json', '完整回归结果.json'),
        (OUT / 'folder-tests.log', '最终目录回归.log'),
    ):
        shutil.copy2(source, RELEASE / destination)
    launcher = (ROOT / 'release/v0.5.0-rc.9/启动候选版.cmd').read_text(encoding='utf-8').replace('0.5.0-rc.9', VERSION)
    (RELEASE / '启动候选版.cmd').write_text(launcher, encoding='utf-8')
    report = {
        'sealed': True, 'version': VERSION, 'schema_revision': 'kb0012',
        **json.loads(checked.stdout), 'dataDir': str(DATA), 'configFile': str(config_file),
        'migration_verified': True, 'actual_workspace_upgrade_pending': False,
        'automatic_backup': False, 'backup_directory_unchanged': True,
        'originals_copied': False, 'indexes_rebuilt': False, 'version_data_copy': False,
        'actual_data_runtime': runtime, 'tests': tests, 'final_directory_regressions': 5, 'checksums': checksums,
    }
    for path in (OUT / 'candidate-seal.json', RELEASE / '当前交付状态.json'):
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'sealed': True, 'version': VERSION, 'fixed_data': str(DATA), 'version_data_copy': False}))


if __name__ == '__main__':
    main()
