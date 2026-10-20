"""Stop only orphan PostgreSQL servers belonging to our packaged GUI fixtures."""
import ctypes,json,os,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def argv(line):
    parse=ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_int)];parse.restype=ctypes.POINTER(ctypes.c_wchar_p)
    count=ctypes.c_int();pointer=parse(line,ctypes.byref(count))
    if not pointer:return []
    try:return [pointer[i] for i in range(count.value)]
    finally:ctypes.windll.kernel32.LocalFree(ctypes.cast(pointer,ctypes.c_void_p))


def main():
    bundle=(ROOT/'release/v0.5.0-rc.6/win-unpacked/resources/postgres/bin/postgres.exe').resolve()
    command="[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process -Filter \"Name='postgres.exe'\" | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    raw=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW,check=True).stdout.decode('utf-8-sig')
    processes=json.loads(raw) if raw.strip() else [];processes=[processes] if isinstance(processes,dict) else processes
    candidates={int(v['ProcessId']):v for v in processes or [] if v.get('ExecutablePath') and Path(v['ExecutablePath']).resolve()==bundle}
    stopped=0
    runtime=ROOT/'build/postgres';environment=os.environ.copy()
    environment['PATH']=str(runtime/'bin')+os.pathsep+str(runtime/'lib')+os.pathsep+environment.get('PATH','')
    for base in (ROOT/'.local/core-corpus-20261018',ROOT/'.local/assistant-collaboration-20261017'):
        for fixture in base.glob('gui-*'):
            target=fixture.resolve()/'knowledge-db'
            if fixture.is_symlink() or fixture.resolve().parent!=base.resolve():continue
            pid_file=target/'postmaster.pid'
            if not pid_file.is_file():continue
            process=candidates.get(int(pid_file.read_text(encoding='utf-8').splitlines()[0]))
            if not process:continue
            args=argv(process.get('CommandLine') or '')
            if '-D' not in args:continue
            position=args.index('-D')+1
            if position>=len(args) or Path(args[position]).resolve()!=target:continue
            result=subprocess.run([str(runtime/'bin/pg_ctl.exe'),'-D',str(target),'-m','fast','-w','-t','15','stop'],env=environment,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW,timeout=25)
            if result.returncode:raise RuntimeError('隔离测试 PostgreSQL 未正常停止')
            stopped+=1
    print(json.dumps({'stopped_verified_gui_databases':stopped}))


if __name__=='__main__':main()
