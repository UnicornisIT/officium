"""Serve the application with Waitress against a throw-away SQLite database.

    python tests/performance/serve_for_profile.py --threads 4 --port 5055 --workdir DIR

For local resource runs with import_load_driver.py only: it creates the
schema with create_all (no migrations), stores the instance directory in
DIR and prints the server pid. Never point it at a real database.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--port', type=int, default=5055)
    parser.add_argument('--workdir', required=True)
    args = parser.parse_args()
    workdir = Path(args.workdir).resolve()
    (workdir / 'instance').mkdir(parents=True, exist_ok=True)
    os.environ.update({
        'OFFICIUM_ENV': 'development',
        'SECRET_KEY': os.environ.get('SECRET_KEY') or 'local-resource-run-secret-0123456789abcdef',
        'DATABASE_URL': f'sqlite:///{(workdir / "officium.sqlite").as_posix()}',
        'OFFICIUM_INSTANCE_PATH': str(workdir / 'instance'),
        'SESSION_COOKIE_SECURE': 'false',
    })
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from waitress import serve

    from run import app
    from extensions import db

    with app.app_context():
        db.create_all()
    print(f'pid={os.getpid()} threads={args.threads}', flush=True)
    serve(app, host='127.0.0.1', port=args.port, threads=args.threads)


if __name__ == '__main__':
    raise SystemExit(main())
