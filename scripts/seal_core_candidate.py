"""Seal the candidate only after packaged GUI and full corpus verification."""
import hashlib,json,shutil,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def sha(file):
    with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    release=ROOT/'release/v0.5.0-rc.6';output=ROOT/'.local/core-corpus-20261018'
    resources=release/'win-unpacked/resources';backend=resources/'backend/ShixueWorkbenchService.exe'
    if sha(backend)!=sha(ROOT/'build/backend/ShixueWorkbenchService.exe'):raise RuntimeError('包内后端与最终构建不一致')
    gui_path=output/'packaged-gui-report.json';gui=json.loads(gui_path.read_text(encoding='utf-8'))
    if gui['version']!='0.5.0-rc.6' or gui['schema']!='kb0010' or not all(gui[k] for k in ('book_grouping','source_export_identical','evidence_saved','project_link','rag_handoff')):raise RuntimeError('打包界面验收尚未通过')
    if gui_path.stat().st_mtime<max(backend.stat().st_mtime,(resources/'app.asar').stat().st_mtime):raise RuntimeError('验收记录早于最终构建')
    data=json.loads((output/'candidate-data-verification.json').read_text(encoding='utf-8'))
    if not data['ready'] or data['books']!=15295 or data['original_sha256_verified']!=28950:raise RuntimeError('全量数据验收尚未通过')
    script="""
const fs=require('node:fs'),path=require('node:path'),asar=require('@electron/asar');
const archive=process.argv[1],dist=process.argv[2];
const pkg=JSON.parse(asar.extractFile(archive,'package.json'));if(pkg.version!=='0.5.0-rc.6')throw Error('Wrong version');
let checked=0;function verify(folder){for(const entry of fs.readdirSync(folder,{withFileTypes:true})){
const source=path.join(folder,entry.name);if(entry.isDirectory()){verify(source);continue;}
const relative=path.relative(dist,source).split(path.sep).join('/');
if(!fs.readFileSync(source).equals(asar.extractFile(archive,path.join('dist',relative))))throw Error('Frontend mismatch: '+relative);checked++;
}}verify(dist);process.stdout.write(JSON.stringify({version:pkg.version,frontend_files_verified:checked}));
"""
    result=subprocess.run(['node','-e',script,str(resources/'app.asar'),str(ROOT/'dist')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    if result.returncode:raise RuntimeError('包内前端校验失败：'+result.stderr[:1000])
    files=[release/'史学工作台候选版 0.5.0-rc.6.exe',release/'史学工作台候选版 Setup 0.5.0-rc.6.exe']
    checksums={file.name:sha(file) for file in files}
    (release/'SHA256.json').write_text(json.dumps(checksums,ensure_ascii=False,indent=2),encoding='utf-8')
    shutil.copy2(ROOT/'核心古籍库-0.5.0-rc.6-实施与使用.md',release/'使用与验收.md')
    shutil.copy2(output/'candidate-data-verification.json',release/'古籍导入验收.json')
    launcher=(ROOT/'release/v0.5.0-rc.5/启动候选版.cmd').read_text(encoding='utf-8').replace('0.5.0-rc.5','0.5.0-rc.6')
    (release/'启动候选版.cmd').write_text(launcher,encoding='utf-8')
    sealed={**json.loads(result.stdout),'backend_sha256':sha(backend),'backend_matches_build':True,'full_corpus_verified':True,'packaged_gui_verified':True,'checksums':checksums}
    (output/'candidate-seal.json').write_text(json.dumps(sealed,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sealed':True,'version':sealed['version'],'frontend_files_verified':sealed['frontend_files_verified'],'books':data['books'],'files':data['files']}))


if __name__=='__main__':main()
