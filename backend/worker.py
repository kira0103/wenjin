"""Independent execution process for a standalone personal workspace."""
import argparse
import logging
import json
import os
import time
import signal
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, application, stop=None):
        self.application = application
        self.stop = stop or threading.Event()

    def recover(self):
        """Called only under the process lease; never retry an uncertain model call."""
        app = self.application
        with app.connection() as db:
            db.execute("UPDATE tasks SET status='paused',error='执行进程中断，请核对后重试' WHERE status='running'")
            db.execute("UPDATE preprocess_jobs SET status='paused',error='执行进程中断，可继续识别' WHERE status='running'")
            db.execute("UPDATE collation_debug_runs SET status='interrupted',error='执行进程中断，响应及用量未知' WHERE status='running'")
            app.collation.recover(db, app.utc_now)

    def tick(self):
        app = self.application
        # Each business engine retains its own durable states and merge rules.
        with app.connection() as db:
            task = db.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            preprocess = db.execute("SELECT id FROM preprocess_jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            debug = db.execute("SELECT id FROM collation_debug_runs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if debug:
            app.debug_controller.execute(app.service.root, debug['id'])
        app.batch_runner.kick()
        if task and not self.stop.is_set():
            app.task_worker(task['id'])
        if preprocess and not self.stop.is_set():
            app.preprocess_bindings['execute_job'](preprocess['id'])

    def run(self):
        app = self.application
        from .knowledge.worker import worker
        self.recover()
        heartbeat_stop = threading.Event()
        def heartbeat():
            path = app.service.root / 'worker-status.json'
            temporary = path.with_suffix('.tmp')
            while not heartbeat_stop.is_set():
                try:
                    temporary.write_text(json.dumps({'pid': os.getpid(), 'updated_at': time.time()}), encoding='utf-8')
                    os.replace(temporary, path)
                except OSError:
                    logger.warning('无法更新执行进程状态')
                heartbeat_stop.wait(3)
        heartbeat_thread = threading.Thread(target=heartbeat, name='worker-status', daemon=True)
        heartbeat_thread.start()
        worker.enabled = True
        worker.start(app.service.root)
        try:
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    logger.exception('后台任务轮询失败')
                self.stop.wait(.5)
        finally:
            worker.stop()
            # Pause collation at a durable boundary before releasing the process lease.
            app.batch_runner.shutdown_roots.add(app.service.root)
            with app.connection() as db:
                db.execute("UPDATE collation_batches SET status='pausing' WHERE status='running'")
            for thread in list(app.service.tasks):
                thread.join()
            if worker.thread:
                worker.thread.join()
            heartbeat_stop.set()
            heartbeat_thread.join()
            (app.service.root / 'worker-status.json').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--stop-file', type=Path, help='由工作区宿主发起正常停止的私有标记文件')
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    if not (root / 'library.sqlite').is_file():
        parser.error('请先启动 backend.server 初始化此工作区')
    from .knowledge.managed import DirectoryLease
    lease = DirectoryLease(root / '.execution-worker.lock')
    lease.acquire()
    try:
        from . import app as application
        # The API performs schema setup. Starting a worker must not migrate live data.
        application.service.root = root
        application.service.mode = 'worker'
        application.service.embedded_workers = True
        stop = threading.Event()
        watcher = None
        if args.stop_file:
            def watch_stop():
                while not stop.wait(.5):
                    if args.stop_file.exists():
                        stop.set()
            watcher = threading.Thread(target=watch_stop, daemon=True)
            watcher.start()
        for kind in (signal.SIGINT, signal.SIGTERM):
            signal.signal(kind, lambda *_: stop.set())
        logging.basicConfig(level=logging.INFO)
        try:
            Supervisor(application, stop).run()
        finally:
            stop.set()
            if watcher:
                watcher.join(timeout=2)
    finally:
        lease.release()


if __name__ == '__main__':
    main()
