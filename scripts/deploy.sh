#!/bin/bash
# Compatibility entry point for a manual, exact-tag update. The actual update
# is performed by the installed root-owned runner and versioned release builder.

set -euo pipefail

RELEASE_TAG=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --release) RELEASE_TAG="${2:-}"; shift 2 ;;
        *) echo "Unknown deploy option: $1" >&2; exit 2 ;;
    esac
done

[ -n "$RELEASE_TAG" ] || { echo "Deployment requires --release <tag>." >&2; exit 2; }
[[ "$RELEASE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ ]] || { echo "Unsafe release tag." >&2; exit 2; }
[[ "$RELEASE_TAG" != *..* && "$RELEASE_TAG" != *@\{* && "$RELEASE_TAG" != *.lock ]] || { echo "Unsafe release tag." >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "Run the manual recovery update as root; the web process never uses this script or sudo." >&2; exit 1; }
[ -x /usr/local/sbin/officium-update-runner ] || { echo "Install the Officium updater service first." >&2; exit 1; }

exec /usr/local/sbin/officium-update-runner --start "$RELEASE_TAG"
