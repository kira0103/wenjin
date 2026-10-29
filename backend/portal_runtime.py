"""Internal process adapters used by an isolated portal workspace host."""
import argparse
import os
import sys


def main():
    role = sys.argv.pop(1)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--data-dir', required=True)
    args, _ = parser.parse_known_args()
    from pathlib import Path
    if os.environ.get('SHIXUE_PORTAL_OWNED_DATABASE') == '1':
        from .knowledge.config import register_managed
        register_managed(Path(args.data_dir).resolve(), os.environ['SHIXUE_KB_DATABASE_URL'])
    if role == 'api':
        from .server import main as run
    elif role == 'worker':
        from .worker import main as run
    else:
        raise SystemExit('Unknown workspace process role')
    run()


if __name__ == '__main__':
    main()
