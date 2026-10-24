"""Short-lived storage of bank statement import previews.

A preview is one small JSON file per upload in IMPORT_STORAGE_DIR (by default
<instance>/bank_imports, which is <APP_ROOT>/shared/instance/bank_imports on a
managed server). It holds no statement content: only the owner, the creation
time, the number of preview rows and the monthly expense each row may update.
Titles, amounts and dates travel in the confirmation form, as before.

File names:
    u<user id>-<token>.json            preview waiting for confirmation
    u<user id>-<token>.json.claimed    preview being confirmed right now
    .tmp-<random>                      a preview being written
    <token>.json                       preview of an older release (read only)

Every change is a single atomic rename or unlink, so several processes and
the cleanup timer can work on the directory at the same time: a preview is
confirmed at most once, and a file being written or confirmed is never seen
half-done. Cleanup only removes regular files whose names match the patterns
above and that are older than their time limit; nothing else in the
directory, and nothing outside it, is ever touched.
"""

import errno
import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from flask import current_app

from app.time_utils import as_utc, utc_now

PAYLOAD_VERSION = 2
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{16,64}$')
PREVIEW_RE = re.compile(r'^u(?P<user>[0-9]{1,20})-(?P<token>[A-Za-z0-9_-]{16,64})\.json$')
CLAIMED_RE = re.compile(r'^u[0-9]{1,20}-[A-Za-z0-9_-]{16,64}\.json\.claimed$')
TEMPORARY_RE = re.compile(r'^\.tmp-[A-Za-z0-9_-]{8,64}$')
LEGACY_RE = re.compile(r'^[A-Za-z0-9_-]{16,64}\.json$')
# A file still being written is abandoned after this long (a killed worker).
TEMPORARY_FILE_TTL_SECONDS = 15 * 60
DISK_FULL_ERRNOS = {errno.ENOSPC, getattr(errno, 'EDQUOT', errno.ENOSPC), errno.EFBIG}
ACCESS_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS}


class ImportStorageError(Exception):
    """An expected storage failure with a message that is safe to show.

    code is one of: disk_full, quota, unavailable, not_found, expired,
    in_progress, busy.
    """

    status_by_code = {
        'disk_full': 507,
        'quota': 507,
        'unavailable': 503,
        'busy': 503,
        'not_found': 410,
        'expired': 410,
        'in_progress': 409,
    }

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def http_status(self):
        return self.status_by_code.get(self.code, 500)


@dataclass
class StoredPreview:
    token: str
    user_id: int
    created_at: datetime
    row_count: int
    monthly_match_ids: list


@dataclass
class CleanupStats:
    removed: int = 0
    removed_bytes: int = 0
    kept: int = 0
    kept_bytes: int = 0
    errors: int = 0


_parse_slots_lock = threading.Lock()


class parse_slot:
    """Limits how many statements one process parses at the same time.

    Parsing a statement is the most memory-hungry thing the application does;
    on a 1 GiB server two large parses at once must not run side by side.
    """

    def __init__(self, app=None):
        app = app or current_app._get_current_object()
        limit = max(1, int(app.config.get('IMPORT_MAX_CONCURRENT_PARSES', 1)))
        self.wait_seconds = max(0.0, float(app.config.get('IMPORT_PARSE_WAIT_SECONDS', 20)))
        with _parse_slots_lock:
            self.semaphore = app.extensions.setdefault(
                'officium_import_parse_slots', threading.BoundedSemaphore(limit)
            )

    def __enter__(self):
        if not self.semaphore.acquire(timeout=self.wait_seconds):
            raise ImportStorageError(
                'busy',
                'Сервер сейчас обрабатывает другую выписку. Повторите загрузку через минуту.',
            )
        return self

    def __exit__(self, *_exc_info):
        self.semaphore.release()
        return False


def storage_dir(app=None):
    app = app or current_app
    configured = str(app.config.get('IMPORT_STORAGE_DIR') or '').strip()
    return os.path.abspath(configured) if configured else os.path.join(app.instance_path, 'bank_imports')


def ttl_seconds(app=None):
    app = app or current_app
    return max(60, int(app.config.get('IMPORT_PREVIEW_TTL_MINUTES', 240)) * 60)


def _log_os_error(action, exc):
    # errno and the directory only: never file contents or user data.
    current_app.logger.error(
        'Import storage %s failed: errno=%s (%s) dir=%s',
        action, exc.errno, errno.errorcode.get(exc.errno, '?'), storage_dir(),
    )


def _storage_error(action, exc):
    _log_os_error(action, exc)
    if exc.errno in DISK_FULL_ERRNOS:
        return ImportStorageError(
            'disk_full',
            'На сервере закончилось место для временных файлов импорта. '
            'Повторите позже или сообщите администратору.',
        )
    if exc.errno in ACCESS_ERRNOS:
        return ImportStorageError(
            'unavailable',
            'Хранилище импорта на сервере недоступно для записи. Сообщите администратору.',
        )
    return ImportStorageError(
        'unavailable',
        'Не удалось сохранить предпросмотр импорта. Повторите попытку позже.',
    )


def ensure_storage_dir():
    path = storage_dir()
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise _storage_error('directory creation', exc) from exc
    if not os.path.isdir(path) or os.path.islink(path):
        current_app.logger.error('Import storage path is not a directory: %s', path)
        raise ImportStorageError('unavailable', 'Хранилище импорта на сервере настроено неверно. Сообщите администратору.')
    return path


def _preview_path(directory, user_id, token):
    return os.path.join(directory, f'u{int(user_id)}-{token}.json')


def _scan(directory):
    """(name, lstat) of the regular files this module manages."""
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return []
    managed = []
    for entry in entries:
        name = entry.name
        if not (PREVIEW_RE.match(name) or CLAIMED_RE.match(name) or TEMPORARY_RE.match(name) or LEGACY_RE.match(name)):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        managed.append((name, info))
    return managed


def _unlink_quietly(path):
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def cleanup_expired(app=None, now=None):
    """Remove expired previews, stale claims and abandoned temporary files.

    Safe to run from several processes and while imports are in progress:
    a preview younger than the TTL, a claim younger than the TTL (its mtime
    is set when confirmation starts) and a temporary file younger than
    TEMPORARY_FILE_TTL_SECONDS are kept.
    """
    app = app or current_app
    now = time.time() if now is None else now
    directory = storage_dir(app)
    stats = CleanupStats()
    preview_limit = ttl_seconds(app)
    for name, info in _scan(directory):
        limit = TEMPORARY_FILE_TTL_SECONDS if TEMPORARY_RE.match(name) else preview_limit
        if now - info.st_mtime < limit:
            stats.kept += 1
            stats.kept_bytes += info.st_size
            continue
        try:
            if _unlink_quietly(os.path.join(directory, name)):
                stats.removed += 1
                stats.removed_bytes += info.st_size
        except OSError as exc:
            stats.errors += 1
            app.logger.warning(
                'Import cleanup could not remove a file: errno=%s dir=%s', exc.errno, directory,
            )
    return stats


def _check_limits(directory, user_id, incoming_bytes):
    app = current_app
    managed = _scan(directory)
    user_prefix = f'u{int(user_id)}-'
    pending = sorted(
        ((info.st_mtime, name) for name, info in managed
         if name.startswith(user_prefix) and PREVIEW_RE.match(name)),
    )
    # A new upload supersedes the user's oldest unconfirmed previews.
    per_user = max(1, int(app.config.get('IMPORT_MAX_PENDING_PER_USER', 5)))
    superseded = set()
    while len(pending) >= per_user:
        _mtime, name = pending.pop(0)
        try:
            _unlink_quietly(os.path.join(directory, name))
            superseded.add(name)
        except OSError as exc:
            _log_os_error('superseded preview removal', exc)
            break

    remaining = [(name, info) for name, info in managed if name not in superseded]
    total_bytes = sum(info.st_size for _name, info in remaining)
    max_bytes = int(app.config.get('IMPORT_STORAGE_MAX_BYTES', 32 * 1024 * 1024))
    max_files = int(app.config.get('IMPORT_STORAGE_MAX_FILES', 500))
    if len(remaining) + 1 > max_files or total_bytes + incoming_bytes > max_bytes:
        app.logger.warning(
            'Import storage quota reached: files=%s bytes=%s limit_files=%s limit_bytes=%s',
            len(remaining), total_bytes, max_files, max_bytes,
        )
        raise ImportStorageError(
            'quota',
            'Временное хранилище импорта заполнено. Повторите загрузку позже.',
        )

    reserve = int(app.config.get('IMPORT_DISK_RESERVE_BYTES', 512 * 1024 * 1024))
    try:
        free = shutil.disk_usage(directory).free
    except OSError as exc:
        raise _storage_error('free space check', exc) from exc
    if free - incoming_bytes < reserve:
        app.logger.error(
            'Import refused: free=%s bytes is below the reserve of %s bytes on %s',
            free, reserve, directory,
        )
        raise ImportStorageError(
            'disk_full',
            'На сервере осталось мало свободного места, импорт временно недоступен. Сообщите администратору.',
        )


def save_preview(user_id, rows):
    """Store a preview of `rows` (parsed import rows) and return its token."""
    directory = ensure_storage_dir()
    payload = {
        'version': PAYLOAD_VERSION,
        'user_id': int(user_id),
        'created_at': utc_now().replace(tzinfo=None).isoformat(),
        'row_count': len(rows),
        'monthly_match_ids': [getattr(row, 'monthly_match_id', None) for row in rows],
    }
    data = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    _check_limits(directory, user_id, len(data))

    token = secrets.token_urlsafe(24)
    temporary = os.path.join(directory, f'.tmp-{secrets.token_urlsafe(12)}')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, _preview_path(directory, user_id, token))
    except OSError as exc:
        try:
            _unlink_quietly(temporary)
        except OSError:
            pass
        raise _storage_error('preview write', exc) from exc
    return token


def _read_payload(path):
    try:
        with open(path, 'rb') as handle:
            raw = handle.read(1024 * 1024 + 1)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _storage_error('preview read', exc) from exc
    if len(raw) > 1024 * 1024:
        raise ImportStorageError('not_found', 'Импорт не найден. Загрузите выписку еще раз.')
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        current_app.logger.warning('Import preview file is unreadable: %s', os.path.basename(path))
        raise ImportStorageError('not_found', 'Импорт не найден. Загрузите выписку еще раз.')
    if not isinstance(payload, dict):
        raise ImportStorageError('not_found', 'Импорт не найден. Загрузите выписку еще раз.')
    return payload


def _preview_from_payload(token, payload):
    if payload.get('version') == PAYLOAD_VERSION:
        row_count = int(payload.get('row_count', 0))
        match_ids = list(payload.get('monthly_match_ids') or [])
    else:
        # Files written by releases before this storage format.
        rows = payload.get('rows') or []
        row_count = len(rows)
        match_ids = [row.get('monthly_match_id') if isinstance(row, dict) else None for row in rows]
    match_ids = (match_ids + [None] * row_count)[:row_count]
    try:
        created_at = as_utc(datetime.fromisoformat(str(payload.get('created_at'))))
    except ValueError:
        raise ImportStorageError('not_found', 'Импорт не найден. Загрузите выписку еще раз.')
    return StoredPreview(
        token=token,
        user_id=payload.get('user_id'),
        created_at=created_at,
        row_count=row_count,
        monthly_match_ids=match_ids,
    )


class claimed_preview:
    """Exclusive use of a preview while its import is being confirmed.

        with claimed_preview(user_id, token) as preview:
            ...write the expenses and commit...
            claim.complete()

    The preview file is renamed to *.claimed first, so a second confirmation
    of the same preview (a double click, a resent form) finds nothing and
    cannot import the rows twice. complete() deletes it; leaving the block
    without complete() (validation error, database failure) puts it back so
    the user can correct the form and confirm again.
    """

    def __init__(self, user_id, token):
        if not token or not TOKEN_RE.match(token):
            raise ImportStorageError('not_found', 'Импорт не найден. Загрузите выписку еще раз.')
        self.user_id = int(user_id)
        self.token = token
        self.directory = storage_dir()
        self.source = None
        self.claimed = None
        self.completed = False

    def _claim(self):
        candidates = (
            _preview_path(self.directory, self.user_id, self.token),
            os.path.join(self.directory, f'{self.token}.json'),
        )
        for source in candidates:
            claimed = source + '.claimed'
            try:
                os.rename(source, claimed)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise _storage_error('preview claim', exc) from exc
            try:
                os.utime(claimed)
            except OSError:
                pass
            self.source, self.claimed = source, claimed
            return
        if os.path.exists(candidates[0] + '.claimed'):
            raise ImportStorageError(
                'in_progress',
                'Этот импорт уже подтверждается. Обновите страницу расходов через несколько секунд.',
            )
        raise ImportStorageError('not_found', 'Импорт не найден или устарел. Загрузите выписку еще раз.')

    def __enter__(self):
        self._claim()
        try:
            payload = _read_payload(self.claimed)
            preview = _preview_from_payload(self.token, payload)
        except FileNotFoundError:
            raise ImportStorageError('not_found', 'Импорт не найден или устарел. Загрузите выписку еще раз.')
        except Exception:
            self._release()
            raise
        if preview.user_id != self.user_id:
            # A legacy <token>.json of another user: give it back untouched.
            self._release()
            raise ImportStorageError('not_found', 'Этот предпросмотр импорта принадлежит другому пользователю.')
        if (utc_now() - preview.created_at).total_seconds() > ttl_seconds():
            self.complete()
            raise ImportStorageError('expired', 'Предпросмотр импорта устарел. Загрузите выписку еще раз.')
        return preview

    def complete(self):
        if self.claimed and not self.completed:
            try:
                _unlink_quietly(self.claimed)
            except OSError as exc:
                _log_os_error('confirmed preview removal', exc)
            self.completed = True

    def _release(self):
        if self.claimed and not self.completed:
            try:
                os.rename(self.claimed, self.source)
            except OSError as exc:
                _log_os_error('preview release', exc)
            self.completed = True

    def __exit__(self, exc_type, _exc, _traceback):
        if not self.completed:
            self._release()
        return False


def discard_preview(user_id, token):
    if not token or not TOKEN_RE.match(token):
        return
    try:
        _unlink_quietly(_preview_path(storage_dir(), user_id, token))
    except OSError as exc:
        _log_os_error('preview removal', exc)


@dataclass
class DiskStatus:
    path: str
    total: int
    free: int
    level: str  # ok | warning | critical


def disk_status(path, warn_free, critical_free):
    usage = shutil.disk_usage(path)
    if usage.free < critical_free:
        level = 'critical'
    elif usage.free < warn_free:
        level = 'warning'
    else:
        level = 'ok'
    return DiskStatus(path=path, total=usage.total, free=usage.free, level=level)
