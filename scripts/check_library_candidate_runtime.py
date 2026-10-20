"""Read checks of the final packaged service against the independent full candidate data."""
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'release/v0.5.0-rc.9'
DATA = ROOT / '工作台数据'
OUTPUT = ROOT / '.local/categories-rc9'


def main():
    if (DATA / '.candidate-incomplete').exists() or (DATA / 'knowledge-db/postmaster.pid').exists():
        raise RuntimeError('资料副本未完成或正在使用，未启动测试服务')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    token = secrets.token_hex(32)
    environment = {**os.environ, 'HF_HUB_OFFLINE': '1'}
    environment.pop('SHIXUE_KB_DATABASE_URL', None)
    child = subprocess.Popen([str(RELEASE / 'win-unpacked/resources/backend/ShixueWorkbenchService.exe'),
                              '--port', str(port), '--data-dir', str(DATA), '--token', token, '--database-mode', 'internal'],
                             env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'X-App-Token': token}, timeout=20)
    try:
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError('打包服务提前退出')
            try:
                if client.get('/health').is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError('打包服务未就绪')
        def get(path):
            response = client.get(path); response.raise_for_status(); return response.json()
        overview = get('/api/submissions/overview')
        health = get('/api/knowledge/health')
        categories = get('/api/knowledge/literature-categories')
        core = get('/api/knowledge/core/status')
        projects = get('/api/bootstrap?project_id=project-main')['projects']
        # Reimport preview only: byte-identical original CSV must be recognized as already imported.
        content = (ROOT / 'backend/data/CSSCI_2025-2026.csv').read_bytes().decode('utf-8')
        response = client.post('/api/submissions/import/preview', json={'content': content, 'label': '原始目录重复导入核验'})
        response.raise_for_status(); preview = response.json()
        response = client.post('/api/knowledge/core/search', json={'query': '學而時習之', 'mode': 'exact', 'limit': 10})
        response.raise_for_status(); result = response.json()
        hits = result.get('results', result.get('items', []))
        started = time.monotonic()
        response = client.post('/api/knowledge/search', json={'query':'學而時習之','mode':'keyword',
                               'scope':{'include_core':True,'library_kind':'ancient'},'source_kinds':['document'],'limit':10})
        response.raise_for_status(); unified = response.json()
        report = {'version': get('/health')['version'], 'ready': health.get('ready'),
                  'schema_revision':health.get('schema_revision'),
                  'literature_category_count':len(categories['items']),
                  'literature_counts':categories['all'],
                  'uncategorized_counts':categories['uncategorized'],
                  'journals': len(overview['journals']), 'manuscripts': len(overview['papers']),
                  'projects': len(projects), 'core_corpora': len(core['items']),
                  'original_csv_already_imported': preview['already_imported'], 'original_csv_new_journals': preview['new'],
                  'core_original_search_hits': len(hits), 'unified_global_hits':len(unified['results']),
                  'unified_global_seconds':round(time.monotonic()-started,3),
                  'data_outside_release': 'release' not in DATA.parts, 'cloud_model_calls': 0}
        if not (report['ready'] and report['schema_revision']=='kb0011' and report['journals'] >= 935
                and preview['already_imported'] and preview['new'] == 0 and hits and unified['results']):
            raise RuntimeError('最终实际资料运行核验未通过：'+json.dumps(report))
        (OUTPUT / 'fixed-runtime-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report))
    finally:
        try:
            client.post('/api/service/exit'); child.wait(timeout=35)
        finally:
            client.close()
        if (DATA / 'knowledge-db/postmaster.pid').exists():
            raise RuntimeError('资料数据库尚未停止')


if __name__ == '__main__':
    main()
