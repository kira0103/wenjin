"""Verify rc.9 artifacts and install only the stable workspace pointer, never another data copy."""
import json
import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.5.0-rc.9'
OUT=ROOT/'.local/categories-rc9'
RELEASE=ROOT/'release'/('v'+VERSION)
DATA=ROOT/'工作台数据'


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def read(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


def main():
    resources=RELEASE/'win-unpacked/resources'
    backend=resources/'backend/ShixueWorkbenchService.exe'
    if sha(backend)!=sha(ROOT/'build/backend/ShixueWorkbenchService.exe'):raise RuntimeError('包内后端不匹配')
    gui=read('packaged-gui-report.json');runtime=read('fixed-runtime-report.json');bootstrap=read('bootstrap-gui-report.json');tests=read('full-result.json')
    if tests['errors'] or tests['failures']:raise RuntimeError('回归未通过')
    if gui['version']!=VERSION or gui['library_tabs']!=['古籍','论文','统一检索'] or not all(gui[k] for k in ('project_result_saved','unlinked_core_in_global_search','notes_not_created','storage_controls','custom_skill_used','custom_agent_bound')):raise RuntimeError('打包界面未通过')
    if not all(gui[k] for k in ('category_tree_crud','multiple_memberships','bulk_classification','import_category_destination','category_search','category_research_handoff','category_delete_retained_works')):raise RuntimeError('文献分类界面未通过')
    category_upgrade=read('category-upgrade-report.json')
    if category_upgrade['to_schema']!='kb0011' or not category_upgrade['existing_data_equal']:raise RuntimeError('原有资料升级核验未通过')
    backup_verification=read('category-backup-verification.json')
    if not backup_verification['all_files_verified']:raise RuntimeError('升级前完整备份尚未独立核验')
    if not bootstrap['packaged_main_verified'] or len(bootstrap['launches'])!=2 or not bootstrap['stable_workspace']:raise RuntimeError('主进程路径验证未通过')
    if runtime['version']!=VERSION or not runtime['ready'] or not runtime['data_outside_release'] or runtime['journals']<935 or not runtime['unified_global_hits']:raise RuntimeError('实际固定资料运行验证未通过')
    for name in ('packaged-gui-report.json','fixed-runtime-report.json'):
        if (OUT/name).stat().st_mtime<backend.stat().st_mtime:raise RuntimeError('运行证据早于最终后端')
    if (OUT/'packaged-gui-report.json').stat().st_mtime<(resources/'app.asar').stat().st_mtime:raise RuntimeError('界面验证早于打包')
    migration=json.loads((DATA/'workspace-migration.json').read_text(encoding='utf-8'))
    if not migration['sqlite_rows_verified'] or not migration['source_preserved']:raise RuntimeError('资料迁移未完成')
    if (RELEASE/'史学工作台验证数据').exists() or (RELEASE/'工作台数据').exists():raise RuntimeError('发布目录包含重复资料')
    script=r'''
const fs=require('node:fs'),path=require('node:path'),asar=require('@electron/asar');
const archive=process.argv[1],dist=process.argv[2];
const pkg=JSON.parse(asar.extractFile(archive,'package.json'));if(pkg.version!=='0.5.0-rc.9')throw Error('Wrong version');
let checked=0;function walk(folder){for(const entry of fs.readdirSync(folder,{withFileTypes:true})){
const file=path.join(folder,entry.name);if(entry.isDirectory()){walk(file);continue;}
if(!fs.readFileSync(file).equals(asar.extractFile(archive,path.join('dist',path.relative(dist,file)))))throw Error('Frontend mismatch');checked++;
}}walk(dist);for(const name of ['main.cjs','preload.cjs','workspace-path.cjs'])if(!fs.readFileSync(path.join('electron',name)).equals(asar.extractFile(archive,path.join('electron',name))))throw Error('Electron mismatch');
process.stdout.write(JSON.stringify({frontend_files_verified:checked,electron_files_verified:3}));
'''
    checked=subprocess.run(['node','-e',script,str(resources/'app.asar'),str(ROOT/'dist')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
    executables=[RELEASE/f'史学工作台候选版 {VERSION}.exe',RELEASE/f'史学工作台候选版 Setup {VERSION}.exe']
    if any(p.stat().st_mtime<backend.stat().st_mtime for p in executables):raise RuntimeError('发布文件过期')
    checksums={p.name:sha(p) for p in executables}
    # The user explicitly authorized this one-time move and stable startup configuration.
    configure=r'''
const fs=require('node:fs'),path=require('node:path'),storage=require('./electron/workspace-path.cjs');
const file=path.join(process.env.APPDATA,'shixue-workbench','bootstrap.json'),data=path.resolve(process.argv[1]);
if(fs.existsSync(file)){const existing=JSON.parse(fs.readFileSync(file,'utf8'));if(path.resolve(existing.dataDir)!==data)throw Error('Existing workspace configuration points elsewhere; not overwritten');}
storage.writeConfig(file,data);if(storage.readConfig(file)!==data)throw Error('Config verification failed');
process.stdout.write(JSON.stringify({configFile:file,dataDir:data}));
'''
    installed=subprocess.run(['node','-e',configure,str(DATA)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
    (RELEASE/'SHA256.json').write_text(json.dumps(checksums,ensure_ascii=False,indent=2),encoding='utf-8')
    for source,destination in ((ROOT/f'文献分类与研究方向-{VERSION}-实施与使用.md',RELEASE/'使用与验收.md'),
                               (OUT/'packaged-gui-report.json',RELEASE/'文献库界面验收.json'),
                               (OUT/'bootstrap-gui-report.json',RELEASE/'固定目录启动验收.json'),
                               (OUT/'fixed-runtime-report.json',RELEASE/'实际资料运行验收.json'),
                               (OUT/'category-upgrade-report.json',RELEASE/'分类升级与数据保留验收.json'),
                               (OUT/'category-backup-verification.json',RELEASE/'升级前备份校验.json'),
                               (DATA/'workspace-migration.json',RELEASE/'资料迁移验收.json')):
        shutil.copy2(source,destination)
    launcher=(ROOT/'release/v0.5.0-rc.7/启动候选版.cmd').read_text(encoding='utf-8').replace('0.5.0-rc.7',VERSION)
    (RELEASE/'启动候选版.cmd').write_text(launcher,encoding='utf-8')
    report={'version':VERSION,**json.loads(checked.stdout),**json.loads(installed.stdout),
            'migration_verified':True,'actual_data_runtime':runtime,'tests':tests,'checksums':checksums,'version_data_copy':False}
    (OUT/'candidate-seal.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (RELEASE/'当前交付状态.json').write_text(json.dumps({**report,'actual_workspace_upgrade_pending':False},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sealed':True,'version':VERSION,'fixed_data':str(DATA),'version_data_copy':False}))


if __name__=='__main__':main()
