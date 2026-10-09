#!/bin/bash

set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/debt_manager}"
DEPLOY_REMOTE="${DEPLOY_REMOTE:-origin}"
SERVICE_NAME="${SERVICE_NAME:-debt_manager}"
RELEASE_TAG=""
SKIP_RESTART="false"

report_deploy_stage() {
    local stage="$1"
    if [ -z "${OFFICIUM_DEPLOY_STAGE_FILE:-}" ]; then
        return
    fi
    case "$stage" in
        preparing|installing|dependencies|migrating) ;;
        *) echo "Invalid internal deployment stage." >&2; exit 2 ;;
    esac
    umask 077
    printf '%s\n' "$stage" > "$OFFICIUM_DEPLOY_STAGE_FILE"
}

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

if [ -z "$RELEASE_TAG" ]; then
    echo "Deployment requires --release <tag>." >&2
    exit 2
fi
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

# A release checkout may replace scripts/deploy.sh itself. Execute a complete
# temporary copy so Bash never reads a partially replaced script.
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

cd "$APP_DIR"
report_deploy_stage preparing

echo "Checking the Git working tree..."
if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
    echo "Tracked local changes found. Release update refused." >&2
    exit 1
fi

echo "Fetching exact release $RELEASE_TAG from Git..."
report_deploy_stage installing
git fetch --force "$DEPLOY_REMOTE" "refs/tags/$RELEASE_TAG:refs/tags/$RELEASE_TAG"
release_commit=$(git rev-parse --verify "refs/tags/$RELEASE_TAG^{commit}")
git checkout --detach "$release_commit"
checked_out_commit=$(git rev-parse --verify HEAD)
if [ "$checked_out_commit" != "$release_commit" ]; then
    echo "Checked out commit does not match the requested release." >&2
    exit 1
fi

echo "Activating virtual environment..."
if [ -d "venv" ]; then
    # VPS layout used by the original deploy script.
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "Virtual environment is missing; create venv or .venv before deployment." >&2
    exit 1
fi

echo "Installing dependencies..."
report_deploy_stage dependencies
python -m pip install -r requirements.txt

echo "Preparing database migrations..."
report_deploy_stage migrating
export FLASK_APP="${FLASK_APP:-run.py}"

migration_state=$(flask db-preflight)

case "$migration_state" in
    empty)
        echo "Empty database detected; Alembic will create the schema."
        ;;
    stamp_baseline)
        baseline_revision=$(flask db-baseline-revision)
        echo "Existing baseline schema found; stamping Alembic revision $baseline_revision..."
        flask db stamp "$baseline_revision"
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
echo "Verifying migration state..."
flask db current
flask db heads

if [ "$SKIP_RESTART" = "true" ]; then
    echo "Service restart delegated to the external updater."
else
    echo "Restarting service..."
    sudo systemctl reset-failed "$SERVICE_NAME" || true
    sudo systemctl restart "$SERVICE_NAME"

    echo "Service status:"
    sudo systemctl status "$SERVICE_NAME" --no-pager
fi
