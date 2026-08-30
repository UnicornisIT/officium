#!/bin/bash

set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/debt_manager}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-master}"
DEPLOY_REMOTE="${DEPLOY_REMOTE:-origin}"
SERVICE_NAME="${SERVICE_NAME:-debt_manager}"
BASELINE_REVISION="73459c8513a1"
RELEASE_TAG=""
SKIP_RESTART="false"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release)
            [ "$#" -ge 2 ] || { echo "--release requires a tag" >&2; exit 2; }
            RELEASE_TAG="$2"
            shift 2
            ;;
        --skip-restart)
            SKIP_RESTART="true"
            shift
            ;;
        *)
            echo "Unknown deploy option: $1" >&2
            exit 2
            ;;
    esac
done

if [ -n "$RELEASE_TAG" ]; then
    if ! [[ "$RELEASE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ ]] \
        || [[ "$RELEASE_TAG" == *".."* ]] \
        || [[ "$RELEASE_TAG" == *"@{"* ]] \
        || [[ "$RELEASE_TAG" == *.lock ]]; then
        echo "Unsafe or unsupported release tag." >&2
        exit 2
    fi
    if [ "${OFFICIUM_BACKUP_CONFIRMED:-false}" != "true" ]; then
        echo "Release deployment requires a confirmed database backup." >&2
        exit 1
    fi

    # A release checkout may replace deploy.sh itself. Execute a complete temporary
    # copy so Bash never reads a partially replaced script.
    if [ "${OFFICIUM_DEPLOY_TEMP_COPY:-false}" != "true" ]; then
        deploy_copy=$(mktemp "${TMPDIR:-/tmp}/officium-deploy.XXXXXX")
        cp "$0" "$deploy_copy"
        chmod 700 "$deploy_copy"
        set +e
        if [ "$SKIP_RESTART" = "true" ]; then
            OFFICIUM_DEPLOY_TEMP_COPY=true /bin/bash "$deploy_copy" \
                --release "$RELEASE_TAG" --skip-restart
        else
            OFFICIUM_DEPLOY_TEMP_COPY=true /bin/bash "$deploy_copy" --release "$RELEASE_TAG"
        fi
        deploy_status=$?
        set -e
        rm -f "$deploy_copy"
        exit "$deploy_status"
    fi
fi

cd "$APP_DIR"

if [ -n "$RELEASE_TAG" ]; then
    echo "Checking the Git working tree..."
    if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
        echo "Tracked local changes found. Release update refused." >&2
        exit 1
    fi

    echo "Fetching exact release $RELEASE_TAG from Git..."
    git fetch --force "$DEPLOY_REMOTE" "refs/tags/$RELEASE_TAG:refs/tags/$RELEASE_TAG"
    release_commit=$(git rev-parse --verify "refs/tags/$RELEASE_TAG^{commit}")
    git checkout --detach "$release_commit"
    checked_out_commit=$(git rev-parse --verify HEAD)
    if [ "$checked_out_commit" != "$release_commit" ]; then
        echo "Checked out commit does not match the requested release." >&2
        exit 1
    fi
else
    echo "Updating code from Git..."
    git pull "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"
fi

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

if [ "$SKIP_RESTART" = "true" ]; then
    echo "Service restart delegated to the external updater."
else
    echo "Restarting service..."
    sudo systemctl reset-failed "$SERVICE_NAME" || true
    sudo systemctl restart "$SERVICE_NAME"

    echo "Service status:"
    sudo systemctl status "$SERVICE_NAME" --no-pager
fi
