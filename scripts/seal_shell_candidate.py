"""Verify rc.12 bundles and current acceptance evidence without copying user data."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.5.0-rc.12'
OUT=ROOT/'.local/shell-rc12'
RELEASE=ROOT/'release'/('v'+VERSION)
DATA=ROOT/'工作台数据'

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))

def require(value,message):
    if not value:raise RuntimeError(message)

def main():
    resources=RELEASE/'win-unpacked/resources';backend=resources/'backend/ShixueWorkbenchService.exe';asar=resources/'app.asar'
    build=ROOT/'build/backend/ShixueWorkbenchService'
    source={p.relative_to(build).as_posix():sha(p) for p in build.rglob('*') if p.is_file()}
    packaged={p.relative_to(resources/'backend').as_posix():sha(p) for p in (resources/'backend').rglob('*') if p.is_file()}
    require(source==packaged and '_internal/base_library.zip' in source,'后端依赖目录与最终构建不一致')
    gui=read('packaged-gui-report.json');bootstrap=read('bootstrap-gui-report.json');runtime=read('fixed-runtime-report.json');closed=read('startup-close-report.json')
    require(gui['version']==VERSION and gui['schema']=='kb0012','界面版本不正确')
    require(all(gui[k] for k in ('pdf_real_page_count','pdf_next_page','pdf_last_page','pdf_without_parsed_text','clean_file_list','responsive_alignment','reader_maintenance_hidden','core_reader_clean','pdf_continuous_scroll','pdf_200_pages_virtualized','project_assistant_embedded','project_switch_isolation','collapsible_panels','resizable_panels','layout_persisted','light_theme_default','dark_theme_switch','system_theme_switch','theme_persisted',
                                'multi_pdf_import','default_uncategorized','directory_structure','file_scope_search','file_scope_research','group_scope_research','manual_reclassification',
                                'custom_skill_used','custom_agent_bound','skill_export','project_result_saved','core_reuse_no_duplicates')),'文献阅读或研究流程未通过')
    require(gui['team_outcome']=='complete' and len(gui['team_nodes'])==4 and all(n['state']=='succeeded' for n in gui['team_nodes']) and gui['cancelled_inflight_calls']>=2,'协同研究未通过')
    require(bootstrap['packaged_main_verified'] and bootstrap['stable_workspace'] and len(bootstrap['launches'])==2
            and all(Path(v['dataDir']).resolve()==DATA.resolve() and v['no_version_data_copy'] and v['window_ms']<v['connected_ms'] and v['custom_titlebar'] and v['titlebar_draggable'] and v['default_theme']=='light' for v in bootstrap['launches']),'固定目录启动未通过')
    require(runtime['version']==VERSION and runtime['ready'] and runtime['schema_revision']=='kb0012' and runtime['single_file_search_verified'] and runtime['journals']>=935 and runtime['unified_global_hits'] and runtime['core_original_search_hits'],'实际资料运行未通过')
    require(closed['closed_during_startup'] and closed['normal_exit'] and closed['database_stopped'],'启动时退出未正常关闭后台')
    for name in ('packaged-gui-report.json','bootstrap-gui-report.json','fixed-runtime-report.json','startup-close-report.json'):
        require((OUT/name).stat().st_mtime>=max(backend.stat().st_mtime,asar.stat().st_mtime),'验收报告早于最终构建：'+name)
    frontend=(OUT/'frontend-tests.log').read_text(encoding='utf-8');native=(OUT/'project-tests.log').read_text(encoding='utf-8')
    require('16 passed' in frontend and '# pass 4' in frontend and '# fail 0' in frontend,'前端回归未通过')
    require('Ran 4 tests' in native and native.rstrip().endswith('OK'),'项目助手专项回归未通过')
    full=read('full-result.json');require(not full['failures'] and not full['errors'],'完整回归未通过')
    config_file=Path(os.environ['APPDATA'])/'shixue-workbench/bootstrap.json'
    require(Path(json.loads(config_file.read_text(encoding='utf-8-sig'))['dataDir']).resolve()==DATA.resolve(),'固定资料配置改变')
    require(not (DATA/'knowledge-db/postmaster.pid').exists(),'实际资料尚未正常关闭')
    require(not any((RELEASE/n).exists() for n in ('工作台数据','史学工作台验证数据')),'发布目录存在重复资料')
    script=r'''
const fs=require('node:fs'),path=require('node:path'),asar=require('@electron/asar');
const archive=process.argv[1],dist=process.argv[2],version=process.argv[3];
if(JSON.parse(asar.extractFile(archive,'package.json')).version!==version)throw Error('Wrong version');
let count=0;function walk(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){const f=path.join(dir,e.name);if(e.isDirectory())walk(f);else{if(!fs.readFileSync(f).equals(asar.extractFile(archive,path.join('dist',path.relative(dist,f)))))throw Error('Frontend mismatch');count++;}}}walk(dist);
for(const name of ['main.cjs','preload.cjs','workspace-path.cjs'])if(!fs.readFileSync(path.join('electron',name)).equals(asar.extractFile(archive,path.join('electron',name))))throw Error('Electron mismatch');
process.stdout.write(JSON.stringify({frontend_files_verified:count,electron_files_verified:3}));
'''
    checked=subprocess.run(['node','-e',script,str(asar),str(ROOT/'dist'),VERSION],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
    executables=[RELEASE/f'史学工作台候选版 {VERSION}.exe',RELEASE/f'史学工作台候选版 Setup {VERSION}.exe']
    require(all(p.stat().st_mtime>=asar.stat().st_mtime for p in executables),'免安装或安装程序早于最终打包')
    checksums={p.name:sha(p) for p in executables}
    (RELEASE/'SHA256.json').write_text(json.dumps(checksums,ensure_ascii=False,indent=2),encoding='utf-8')
    (RELEASE/'后端依赖校验.json').write_text(json.dumps(packaged,ensure_ascii=False,indent=2),encoding='utf-8')
    # Direct launch avoids electron-builder's portable self-extraction on each run.
    launcher='@echo off\nchcp 65001 >nul\nstart "" "%~dp0win-unpacked\\史学工作台候选版.exe"\n'
    (RELEASE/'启动候选版.cmd').write_text(launcher,encoding='utf-8')
    for name,destination in (
        ('packaged-gui-report.json','文献库阅读验收.json'),('bootstrap-gui-report.json','固定目录启动验收.json'),
        ('fixed-runtime-report.json','实际资料运行验收.json'),('startup-close-report.json','启动退出验收.json'),
        ('frontend-tests.log','前端回归.log'),('project-tests.log','项目助手回归.log'),('full-result.json','完整回归结果.json'),
    ):shutil.copy2(OUT/name,RELEASE/destination)
    for relative in ('README.md','docs/MAINTENANCE.md'):
        destination=RELEASE/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/relative,destination)
    report={'sealed':True,'version':VERSION,'schema_revision':'kb0012',**json.loads(checked.stdout),
            'backend_files_verified':len(source),'dataDir':str(DATA),'actual_workspace_upgrade_pending':False,
            'schema_change':False,'automatic_backup':False,'version_data_copy':False,
            'tests':{'frontend':20,'project':4,'full':full},'actual_data_runtime':runtime,
            'continuous_pdf_verified':True,'project_assistant_verified':True,'theme_switch_verified':True,'custom_titlebar_verified':True,'early_window_verified':True,'startup_close_verified':True,'checksums':checksums}
    for target in (OUT/'candidate-seal.json',RELEASE/'当前交付状态.json'):target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sealed':True,'version':VERSION,'backend_files_verified':len(source),'version_data_copy':False}))

if __name__=='__main__':main()
