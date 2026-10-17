"""Tell a user once what changed in the release running on this server.

The current version comes from ``APP_VERSION`` (the VERSION file), the text
from the matching dated section of the bundled CHANGELOG.md. Each user keeps
the last version they acknowledged in ``users.last_seen_release``; a mismatch
shows the notes of the current release only, never a backlog of older ones.

Everything here is best effort: a missing or unreadable changelog, a release
without its own section or a section without Added/Changed/Fixed simply means
there is nothing to show.
"""

import logging
import threading
from pathlib import Path

from app.services.installed_release_service import changelog_version, parse_changelog
from app.services.release_notes_service import render_release_notes


logger = logging.getLogger(__name__)

CHANGELOG_PATH = Path(__file__).resolve().parents[2] / 'CHANGELOG.md'
MAX_CHANGELOG_BYTES = 512 * 1024
# The modal shows whole sections; the budget only guards against a runaway file.
_RENDER_BUDGET = 10_000
_MAX_VERSION_LENGTH = 64

# Keep a Changelog groups, in the order they are shown. The project writes
# them in Russian; the English names are accepted as well.
SECTION_KINDS = (
    ('added', 'Добавлено', ('добавлено', 'added')),
    ('changed', 'Изменено', ('изменено', 'changed')),
    ('fixed', 'Исправлено', ('исправлено', 'fixed')),
)
_KIND_BY_HEADING = {name: kind for kind, _title, names in SECTION_KINDS for name in names}

_cache = {}
_cache_lock = threading.Lock()


def normalize_version(value):
    """Return the CHANGELOG form of a version (``v0.0.15-alpha`` -> ``0.0.15-alpha``)."""
    version = changelog_version(value)
    if not version or len(version) > _MAX_VERSION_LENGTH:
        return None
    return version


def split_sections(body):
    """Return ``{kind: markdown}`` for the Added/Changed/Fixed groups of a release body."""
    groups = {}
    kind = None
    lines = []

    def flush():
        text = '\n'.join(lines).strip()
        if kind and text:
            groups[kind] = f'{groups[kind]}\n\n{text}' if kind in groups else text

    for line in str(body or '').splitlines():
        if line.startswith('### '):
            flush()
            kind = _KIND_BY_HEADING.get(line[4:].strip().casefold())
            lines = []
        elif kind:
            lines.append(line)
    flush()
    return groups


def build_release_notes(text, version):
    """Return the "what's new" notes of ``version`` from changelog ``text``, or ``None``."""
    version = normalize_version(version)
    if not version:
        return None
    section = parse_changelog(text).get(version)
    if section is None:
        return None
    released_on, body = section
    groups = split_sections(body)
    sections = []
    for kind, title, _names in SECTION_KINDS:
        if kind not in groups:
            continue
        html, _truncated = render_release_notes(groups[kind], budget=_RENDER_BUDGET)
        if html:
            sections.append({'kind': kind, 'title': title, 'html': html})
    if not sections:
        return None
    return {'version': version, 'released_on': released_on.isoformat(), 'sections': sections}


def _read_text(path):
    try:
        with Path(path).open('rb') as changelog:
            raw = changelog.read(MAX_CHANGELOG_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_CHANGELOG_BYTES:
        logger.warning('CHANGELOG.md is larger than %s bytes; release notes are skipped', MAX_CHANGELOG_BYTES)
        return None
    return raw.decode('utf-8', errors='replace')


def load_release_notes(version, path=None):
    """Read the bundled CHANGELOG.md and return the notes of ``version``, or ``None``."""
    path = Path(path or CHANGELOG_PATH)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(path), mtime, str(version))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    text = _read_text(path)
    notes = build_release_notes(text, version) if text is not None else None
    with _cache_lock:
        if len(_cache) >= 8:
            _cache.clear()
        _cache[key] = notes
    return notes


def should_show(last_seen, current_version):
    """Whether a user who acknowledged ``last_seen`` still has to see ``current_version``."""
    current = normalize_version(current_version)
    if not current:
        return False
    return normalize_version(last_seen) != current


def pending_release_notes(user, current_version, path=None):
    """Return the notes the user has not acknowledged yet, or ``None``.

    Never raises: a broken changelog must not stand between a user and the app.
    """
    try:
        if not should_show(getattr(user, 'last_seen_release', None), current_version):
            return None
        return load_release_notes(current_version, path)
    except Exception:
        logger.exception('Could not prepare the release notes for the current version')
        return None

