#!/bin/bash
# Build a local release mirror for the deployment integration tests:
#   /srv/qa-git/qa/officium.git with tags v90.0.1 .. v90.0.4 made from the
#   code under test (only VERSION differs), and v90.0.5, which builds and
#   migrates but exits when Waitress starts it: failure injection.
# Run as root on a disposable CI machine.

set -euo pipefail

workspace="${1:?usage: make-release-mirror.sh WORKSPACE}"
mirror=/srv/qa-git/qa/officium.git
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

git -C "$workspace" archive HEAD | tar -x -C "$work"
cd "$work"
git init --quiet --initial-branch=qa
git config user.email qa@example.invalid
git config user.name "Officium QA"
for release in 1 2 3 4; do
    printf 'v90.0.%s\n' "$release" >VERSION
    git add -A
    git commit --quiet -m "QA release v90.0.$release"
    git tag "v90.0.$release"
done
printf 'v90.0.5\n' >VERSION
# Builds, migrates and imports normally; only the Waitress process exits.
python3 - <<'PY'
from pathlib import Path

run = Path('run.py')
run.write_text(
    'import sys\n'
    'if "waitress" in sys.argv[0]:\n'
    '    raise SystemExit("QA release v90.0.5 cannot start")\n' + run.read_text(encoding='utf-8'),
    encoding='utf-8',
)
PY
git commit --quiet -am "QA release v90.0.5 that cannot start"
git tag v90.0.5

rm -rf "$mirror"
install -d -m 0755 "$(dirname "$mirror")"
git clone --quiet --bare "$work" "$mirror"
chmod -R a+rX /srv/qa-git
# The application user clones the root-owned mirror over file://. Only the
# mirror is trusted, so ownership checks elsewhere still behave as on a server.
git config --system --add safe.directory "$mirror"
git ls-remote --tags "file://$mirror"
