"""Prepare reviewable rc.9 files while an open workspace prevents its final upgrade."""
import json
import shutil
import subprocess
from seal_library_candidate import ROOT,VERSION,OUT,RELEASE,DATA,sha,read


def main():
    resources=RELEASE/'win-unpacked/resources'
    backend=resources/'backend/ShixueWorkbenchService.exe'
    assert sha(backend)==sha(ROOT/'build/backend/ShixueWorkbenchService.exe')
    gui=read('packaged-gui-report.json');bootstrap=read('bootstrap-gui-report.json');tests=read('full-result.json')
    assert gui['version']==VERSION and gui['schema']=='kb0011'
    flags=('category_tree_crud','multiple_memberships','bulk_classification','import_category_destination','category_search',
           'category_research_handoff','category_delete_retained_works','custom_skill_used','custom_agent_bound','notes_not_created')
    assert all(gui[k] for k in flags)
    assert bootstrap['packaged_main_verified'] and bootstrap['stable_workspace'] and len(bootstrap['launches'])==2
    assert not tests['failures'] and not tests['errors']
    assert (OUT/'packaged-gui-report.json').stat().st_mtime>(resources/'app.asar').stat().st_mtime
    code=r'''
const fs=require('node:fs'),path=require('node:path'),asar=require('@electron/asar');
const archive=process.argv[1],dist=process.argv[2];let count=0;
if(JSON.parse(asar.extractFile(archive,'package.json')).version!=='0.5.0-rc.9')throw Error('Version mismatch');
function walk(folder){for(const entry of fs.readdirSync(folder,{withFileTypes:true})){const file=path.join(folder,entry.name);
if(entry.isDirectory()){walk(file);continue;}if(!fs.readFileSync(file).equals(asar.extractFile(archive,path.join('dist',path.relative(dist,file)))))throw Error('Frontend mismatch');count++;}}
walk(dist);for(const name of ['main.cjs','preload.cjs','workspace-path.cjs'])if(!fs.readFileSync(path.join('electron',name)).equals(asar.extractFile(archive,path.join('electron',name))))throw Error('Main process mismatch');
process.stdout.write(JSON.stringify({frontend_files_verified:count,electron_files_verified:3}));
'''
    checked=subprocess.run(['node','-e',code,str(resources/'app.asar'),str(ROOT/'dist')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
    executables=[RELEASE/f'史学工作台候选版 {VERSION}.exe',RELEASE/f'史学工作台候选版 Setup {VERSION}.exe']
    assert all(p.stat().st_mtime>=backend.stat().st_mtime for p in executables)
    checksums={p.name:sha(p) for p in executables}
    assert not (RELEASE/'工作台数据').exists() and not (RELEASE/'史学工作台验证数据').exists()
    for source,name in [(ROOT/f'文献分类与研究方向-{VERSION}-实施与使用.md','使用说明.md'),
                        (OUT/'packaged-gui-report.json','文献分类与助手界面验收.json'),
                        (OUT/'bootstrap-gui-report.json','固定目录启动验收.json'),
                        (OUT/'full-result.json','后端完整回归.json'),
                        (OUT/'storage-category-tests.log','分类目录迁移补充测试.log'),
                        (OUT/'isolated-upgrade-report.json','隔离升级验收.json')]:shutil.copy2(source,RELEASE/name)
    launcher=(ROOT/'release/v0.5.0-rc.7/启动候选版.cmd').read_text(encoding='utf-8').replace('0.5.0-rc.7',VERSION)
    (RELEASE/'启动候选版.cmd').write_text(launcher,encoding='utf-8')
    (RELEASE/'SHA256.json').write_text(json.dumps(checksums,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'version':VERSION,'package_verified':True,'actual_workspace_upgrade_pending':True,
            'reason':'旧工作台正在使用固定资料库，尚未执行实际资料完整备份与结构升级',
            'data_root':str(DATA),'version_data_copy':False,'tests':tests,**json.loads(checked.stdout),'checksums':checksums}
    (OUT/'candidate-package-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (RELEASE/'当前交付状态.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('version','package_verified','actual_workspace_upgrade_pending','version_data_copy')}))


if __name__=='__main__':main()
