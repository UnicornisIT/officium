#!/bin/bash

set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/debt_manager}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-master}"
SERVICE_NAME="${SERVICE_NAME:-debt_manager}"
BASELINE_REVISION="73459c8513a1"

cd "$APP_DIR"

echo "Updating code from Git..."
git pull origin "$DEPLOY_BRANCH"

echo "Activating virtual environment..."
if [ -d "venv" ]; then
    # VPS layout used by the original deploy script.
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
else
    python3 -m venv venv
    source venv/bin/activate
fi

echo "Installing dependencies..."
python -m pip install -r requirements.txt

echo "Preparing database migrations..."
export FLASK_APP="${FLASK_APP:-run.py}"

migration_state=$(python - <<'PY'
import sys

from app import app
from extensions import db
from sqlalchemy import inspect, text

BASELINE_REVISION = '73459c8513a1'
BASELINE_TABLES = {
    'activity_logs',
    'app_settings',
    'debts',
    'dictionary_entries',
    'expenses',
    'incomes',
    'payments',
    'users',
}
REVISION_ALIASES = {
    '20260502_add_mortgage_debt_type': '20260502_mortgage',
    '20260502_add_activity_log_ip_user_agent': '20260502_log_ip_ua',
}
VERSION_TABLE = 'alembic_version'


def require_baseline_or_empty(user_tables):
    if not user_tables:
        print('empty')
        return

    if BASELINE_TABLES.issubset(user_tables):
        print('stamp_baseline')
        return

    missing = ', '.join(sorted(BASELINE_TABLES - user_tables))
    raise SystemExit(
        'Existing schema is partial and cannot be safely stamped. '
        f'Missing baseline tables: {missing}'
    )


def read_versions(conn):
    return [
        row[0]
        for row in conn.execute(text(f'SELECT version_num FROM {VERSION_TABLE}'))
        if row[0]
    ]


def normalize_revision_aliases(conn):
    for old_revision, new_revision in REVISION_ALIASES.items():
        result = conn.execute(
            text(
                f'UPDATE {VERSION_TABLE} '
                'SET version_num = :new_revision '
                'WHERE version_num = :old_revision'
            ),
            {'old_revision': old_revision, 'new_revision': new_revision},
        )
        if result.rowcount and result.rowcount > 0:
            print(
                f'Normalized Alembic revision {old_revision} -> {new_revision}.',
                file=sys.stderr,
            )


with app.app_context():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    user_tables = tables - {VERSION_TABLE}

    if VERSION_TABLE not in tables:
        require_baseline_or_empty(user_tables)
        raise SystemExit(0)

    columns = {column['name'] for column in inspector.get_columns(VERSION_TABLE)}
    if 'version_num' not in columns:
        with db.engine.begin() as conn:
            row_count = conn.execute(text(f'SELECT COUNT(*) FROM {VERSION_TABLE}')).scalar_one()
            if row_count:
                raise SystemExit(
                    f'{VERSION_TABLE} exists without version_num and contains rows. '
                    'Cannot infer the current migration revision safely.'
                )
            print(
                f'{VERSION_TABLE} exists without version_num; adding the missing column.',
                file=sys.stderr,
            )
            conn.execute(text(f'ALTER TABLE {VERSION_TABLE} ADD COLUMN version_num VARCHAR(32)'))

        require_baseline_or_empty(user_tables)
        raise SystemExit(0)

    with db.engine.begin() as conn:
        normalize_revision_aliases(conn)
        versions = read_versions(conn)

    if versions:
        too_long = [revision for revision in versions if len(revision) >= 32]
        if too_long:
            joined = ', '.join(too_long)
            raise SystemExit(
                'Alembic version_num contains revision ids that are too long '
                f'for this project policy: {joined}'
            )
        if not user_tables:
            raise SystemExit(
                f'{VERSION_TABLE} has version_num={", ".join(versions)}, '
                'but no application tables were found.'
            )
        print(f'Alembic version table found: {", ".join(versions)}.', file=sys.stderr)
        print('ready')
        raise SystemExit(0)

    require_baseline_or_empty(user_tables)
PY
)

case "$migration_state" in
    empty)
        echo "Empty database detected; Alembic will create the schema."
        ;;
    stamp_baseline)
        echo "Existing baseline schema found; stamping Alembic revision $BASELINE_REVISION..."
        flask db stamp "$BASELINE_REVISION"
        ;;
    ready)
        echo "Alembic version table is ready."
        ;;
    *)
        echo "Unknown migration state returned by deploy preflight: $migration_state" >&2
        exit 1
        ;;
esac

echo "Applying migrations..."
flask db upgrade

echo "Restarting service..."
sudo systemctl reset-failed "$SERVICE_NAME" || true
sudo systemctl restart "$SERVICE_NAME"

echo "Service status:"
sudo systemctl status "$SERVICE_NAME" --no-pager
