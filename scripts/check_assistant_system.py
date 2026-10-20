"""Run assistant lifecycle and collaboration regressions with isolated data."""
import argparse,json,os,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--full',action='store_true');parser.add_argument('--native',action='store_true');parser.add_argument('--output',type=Path);args=parser.parse_args()
    output=args.output or ROOT/'.local/assistant-collaboration-20261017';output.mkdir(parents=True,exist_ok=True)
    temporary=ROOT/'.local/temporary';temporary.mkdir(parents=True,exist_ok=True);tempfile.tempdir=str(temporary)
    for name in ('SHIXUE_KB_DATABASE_URL','SHIXUE_KB_INTEGRATION'):os.environ.pop(name,None)
    if args.native:os.environ['SHIXUE_MANAGED_TESTS']='1'
    else:os.environ.pop('SHIXUE_MANAGED_TESTS',None)
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests')) if args.full else unittest.defaultTestLoader.loadTestsFromName('tests.test_assistant_management')
    label='full' if args.full else 'native' if args.native else 'rules'
    with (output/(label+'-tests.log')).open('w',encoding='utf-8') as log:result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    summary={'tests':result.testsRun,'passed':result.testsRun-len(result.skipped)-len(result.failures)-len(result.errors),'skipped':len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),'native':args.native}
    (output/(label+'-result.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary));return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
