"""Run offline regression checks in independent workspace-owned temporary roots."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--rerank',action='store_true')
    options=parser.parse_args()
    output=ROOT/('.local/rerank-context-20261013' if options.rerank else '.local/retrieval-quality-20261013');output.mkdir(parents=True,exist_ok=True)
    temporary=ROOT/'.local/temporary';temporary.mkdir(parents=True,exist_ok=True)
    tempfile.tempdir=str(temporary)
    os.environ.pop('SHIXUE_KB_DATABASE_URL',None)
    os.environ.pop('SHIXUE_KB_INTEGRATION',None)
    if options.native:os.environ['SHIXUE_MANAGED_TESTS']='1'
    else:os.environ.pop('SHIXUE_MANAGED_TESTS',None)
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests')) if options.full else unittest.defaultTestLoader.loadTestsFromName('tests.test_parent_rerank' if options.rerank else 'tests.test_retrieval_quality')
    label='backend' if options.full else 'quality-native' if options.native else 'quality-rules'
    with (output/(label+'-tests.log')).open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    summary={'tests':result.testsRun,'passed':result.testsRun-len(result.skipped)-len(result.failures)-len(result.errors),
             'skipped':len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),'native':options.native}
    (output/(label+'-result.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary));return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
