"""Verify shipped assets equal tested build inputs and record candidate checksums."""
import hashlib,json,shutil,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    release=ROOT/'release/v0.5.0-rc.5';output=ROOT/'.local/assistant-collaboration-20261017'
    resources=release/'win-unpacked/resources';backend=resources/'backend/ShixueWorkbenchService.exe'
    if sha(backend)!=sha(ROOT/'build/backend/ShixueWorkbenchService.exe'):raise RuntimeError('包内后端与验证构建不一致')
    report_path=output/'packaged-gui-report.json';report=json.loads(report_path.read_text(encoding='utf-8'))
    if report['version']!='0.5.0-rc.5' or report['schema']!='kb0009' or not report['custom_skill_used'] or report['team_outcome']!='complete' or report['cancelled_inflight_calls']!=2:raise RuntimeError('包内后端界面验收记录不完整')
    if report_path.stat().st_mtime<max(backend.stat().st_mtime,(resources/'app.asar').stat().st_mtime):raise RuntimeError('界面验收早于最终构建，请重新验收')
    script="""
const fs=require('node:fs'),path=require('node:path'),asar=require('@electron/asar');
const archive=process.argv[1],dist=process.argv[2];
const pkg=JSON.parse(asar.extractFile(archive,'package.json'));
if(pkg.version!=='0.5.0-rc.5')throw Error('Candidate version mismatch');
let checked=0;
function verify(folder){for(const entry of fs.readdirSync(folder,{withFileTypes:true})){
 const source=path.join(folder,entry.name);if(entry.isDirectory()){verify(source);continue;}
 const relative=path.relative(dist,source).split(path.sep).join('/');
 if(!fs.readFileSync(source).equals(asar.extractFile(archive,path.join('dist',relative))))throw Error('Frontend mismatch: '+relative);
 checked++;
}}
verify(dist);process.stdout.write(JSON.stringify({version:pkg.version,frontend_files_verified:checked}));
"""
    checked=subprocess.run(['node','-e',script,str(resources/'app.asar'),str(ROOT/'dist')],cwd=ROOT,capture_output=True,encoding='utf-8',errors='replace')
    if checked.returncode:raise RuntimeError('包内界面核验未完成：'+checked.stderr[:1600])
    files=[release/'史学工作台候选版 0.5.0-rc.5.exe',release/'史学工作台候选版 Setup 0.5.0-rc.5.exe']
    checksums={p.name:sha(p) for p in files}
    (release/'SHA256.json').write_text(json.dumps(checksums,ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copy2(ROOT/'多智能体与Skill-0.5.0-rc.5-使用与验收.md',release/'使用与验收.md')
    if not (release/'启动候选版.cmd').is_file() or not (release/'史学工作台验证数据/library.sqlite').is_file():raise RuntimeError('启动器或独立资料目录缺失')
    result={**json.loads(checked.stdout),'backend_sha256':sha(backend),'backend_matches_tested_build':True,'gui_verified':True,'candidate_checksums':checksums,
            'data_copy_verified':json.loads((output/'candidate-retrieval.json').read_text(encoding='utf-8'))['ready']}
    (output/'candidate-seal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sealed':True,'version':result['version'],'frontend_files_verified':result['frontend_files_verified'],'gui_verified':True,'data_copy_verified':result['data_copy_verified']}))


if __name__=='__main__':main()
