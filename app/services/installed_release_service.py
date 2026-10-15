"""Describe the release installed on this server from local data only.

The notes come from the section of the local CHANGELOG.md that matches the
installed tag, rendered by the same sanitiser as GitHub release notes. Every
source is optional: a missing file, an untagged commit or an unreadable
database simply leaves the corresponding field empty.
"""

import logging
import re
import threading
from datetime import date
from pathlib import Path
from urllib.parse import quote

from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import SQLAlchemyError

from app.services.release_notes_service import render_release_notes
from app.services.server_update_service import (
    ServerUpdateError,
    format_release_day,
    get_commit_details,
    get_last_successful_update,
    validate_repository,
)


logger = logging.getLogger(__name__)

CHANGELOG_NAME = 'CHANGELOG.md'
MAX_CHANGELOG_BYTES = 512 * 1024
_CACHE_LIMIT = 8

_VERSION_TAG_RE = re.compile(
    r'^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)$'
)
_RELEASE_HEADING_RE = re.compile(
    r'^## \[(?P<version>[^\]\s]+)\] - (?P<date>\d{4}-\d{2}-\d{2})\s*$'
)
_REVISION_RE = re.compile(r'^[0-9A-Za-z_]{1,64}$')

_cache = {}
_cache_lock = threading.Lock()


def _empty_notes():
    return {'version': '', 'released_on': '', 'released_label': '', 'notes_html': '', 'notes_truncated': False}


def changelog_version(tag):
    """Map a release tag such as ``v0.0.12`` to its CHANGELOG version ``0.0.12``."""
    match = _VERSION_TAG_RE.fullmatch(str(tag or '').strip())
    return match.group(1) if match else None


def _changelog_path(config):
    app_dir = Path(config.get('SERVER_UPDATE_APP_DIR') or '.').resolve()
    path = (app_dir / CHANGELOG_NAME).resolve()
    if not path.is_relative_to(app_dir):
        return None
    return path


def _read_changelog(path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_CHANGELOG_BYTES:
            return None
        with path.open('rb') as changelog:
            raw = changelog.read(MAX_CHANGELOG_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_CHANGELOG_BYTES:
        return None
    return raw.decode('utf-8', errors='replace')


def parse_changelog(text):
    """Return ``{version: (release date, section body)}`` for dated release headings.

    Only ``## [X.Y.Z] - YYYY-MM-DD`` opens a release section, so ``[Unreleased]``
    and malformed headings never match a version; any other ``## `` heading
    still closes the section above it.
    """
    sections = {}
    current = None
    lines = []
    for line in str(text or '').splitlines():
        if line.startswith('## '):
            if current:
                sections.setdefault(current[0], (current[1], '\n'.join(lines).strip()))
            current, lines = None, []
            match = _RELEASE_HEADING_RE.fullmatch(line.rstrip())
            if match:
                try:
                    released_on = date.fromisoformat(match.group('date'))
                except ValueError:
                    continue
                current = (match.group('version'), released_on)
        elif current:
            lines.append(line)
    if current:
        sections.setdefault(current[0], (current[1], '\n'.join(lines).strip()))
    return sections


def find_installed_release_notes(config, tag):
    """Return the CHANGELOG section of the installed tag, or empty fields."""
    version = changelog_version(tag)
    path = _changelog_path(config)
    if not version or path is None:
        return _empty_notes()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return _empty_notes()

    key = (str(path), mtime, version)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return dict(cached)

    text = _read_changelog(path)
    section = parse_changelog(text).get(version) if text is not None else None
    if section is None:
        result = _empty_notes()
    else:
        released_on, body = section
        notes_html, notes_truncated = render_release_notes(body)
        result = {
            'version': version,
            'released_on': released_on.isoformat(),
            'released_label': format_release_day(released_on),
            'notes_html': notes_html,
            'notes_truncated': notes_truncated,
        }
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = result
    return dict(result)


def get_database_revision(engine):
    """Return the Alembic revision(s) stamped in the database, without a subprocess."""
    try:
        with engine.connect() as connection:
            heads = MigrationContext.configure(connection).get_current_heads()
    except SQLAlchemyError as exc:
        logger.warning('Cannot read the database revision: %s', type(exc).__name__)
        return ''
    revisions = [str(head) for head in heads if _REVISION_RE.fullmatch(str(head))]
    return ', '.join(sorted(revisions))


def get_installed_release(config, current_release, engine=None):
    """Combine local facts about the installed release for the update page."""
    tag = current_release.get('tag')
    details = find_installed_release_notes(config, tag)
    details['commit'] = get_commit_details(config, current_release.get('commit'))
    details['database_revision'] = get_database_revision(engine) if engine is not None else ''
    details['last_update'] = get_last_successful_update(config)
    details['release_url'] = ''
    if tag and details['version']:
        try:
            repository = validate_repository(config.get('SERVER_UPDATE_REPOSITORY'))
        except ServerUpdateError:
            repository = None
        if repository:
            details['release_url'] = (
                f'https://github.com/{repository}/releases/tag/{quote(str(tag), safe="")}'
            )
    return details
