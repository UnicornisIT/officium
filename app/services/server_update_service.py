import json
import logging
import os
import re
import socket
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from app.services.release_notes_service import render_release_notes


class ServerUpdateError(RuntimeError):
    """A safe, user-facing server update error."""


logger = logging.getLogger(__name__)


_REPOSITORY_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
_RELEASE_TAG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$')
_COMMIT_RE = re.compile(r'^[0-9a-f]{7,40}$')
_ACTIVE_STATES = {
    'queued',
    'checking',
    'preparing',
    'running',  # Backward compatibility with statuses written before v0.0.6.
    'backing_up',
    'installing',
    'migrating',
    'restarting',
    'verifying',
}
_IPC_ERROR_MESSAGES = {
    'authorization_failed': 'Недостаточно прав для запуска серверного помощника.',
    'backup_failed': 'Не удалось создать резервную копию. Обновление не начато.',
    'configuration_invalid': 'Конфигурация серверного помощника некорректна.',
    'dependency_failed': 'Не удалось установить зависимости приложения.',
    'deployment_failed': 'Не удалось установить код релиза.',
    'github_unavailable': 'Серверный помощник не смог проверить релиз на GitHub.',
    'healthcheck_failed': 'Новая версия запущена, но не прошла проверку работоспособности.',
    'insufficient_resources': 'Недостаточно места на диске или памяти для безопасного обновления. Обновление не начато.',
    'migration_failed': 'Не удалось выполнить миграции базы данных.',
    'release_rejected': 'Серверный помощник отклонил выбранный релиз.',
    'restart_failed': 'Не удалось перезапустить службу приложения.',
    'rollback_failed': 'Автоматический возврат предыдущей версии не завершён; требуется recovery.',
    'scheduling_failed': 'Systemd не смог запланировать задачу обновления.',
    'update_busy': 'Другое обновление уже выполняется.',
    'ipc_unavailable': 'Служба безопасного обновления недоступна.',
    'ipc_timeout': 'Служба безопасного обновления не ответила вовремя.',
    'protocol_error': 'Служба обновления вернула некорректный ответ.',
    'unexpected_failure': 'Служба обновления завершила запрос с внутренней ошибкой.',
}
_MAX_IPC_RESPONSE_BYTES = 64 * 1024
_START_IPC_TIMEOUT_SECONDS = 45


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_now():
    return _utc_now().isoformat().replace('+00:00', 'Z')


_MONTHS_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)


def format_release_date(value, timezone_name='UTC'):
    """Format a GitHub ISO timestamp as "13 октября 2026, 15:16" in the app timezone."""
    try:
        moment = datetime.fromisoformat(str(value or '').strip().replace('Z', '+00:00'))
    except ValueError:
        return ''
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        moment = moment.astimezone(ZoneInfo(str(timezone_name or 'UTC')))
    except (ZoneInfoNotFoundError, ValueError):
        moment = moment.astimezone(timezone.utc)
    return f'{moment.day} {_MONTHS_GENITIVE[moment.month - 1]} {moment.year}, {moment:%H:%M}'


def validate_repository(value):
    repository = str(value or '').strip()
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ServerUpdateError('Некорректно настроен GitHub-репозиторий обновлений.')
    return repository


def validate_release_tag(value):
    tag = str(value or '').strip()
    if (
        not _RELEASE_TAG_RE.fullmatch(tag)
        or '..' in tag
        or '@{' in tag
        or tag.endswith('.lock')
    ):
        raise ServerUpdateError('GitHub вернул небезопасный или неподдерживаемый тег релиза.')
    return tag


def fetch_latest_release(config):
    """Return the latest published full GitHub release configured for this server."""
    repository = validate_repository(config.get('SERVER_UPDATE_REPOSITORY'))
    timeout = max(2, min(int(config.get('SERVER_UPDATE_HTTP_TIMEOUT_SECONDS', 8)), 30))
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'officium-server-updater',
    }
    try:
        response = requests.get(
            f'https://api.github.com/repos/{repository}/releases/latest',
            headers=headers,
            timeout=(3.05, timeout),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ServerUpdateError(
            'Не удалось проверить последний релиз на GitHub. Повторите попытку позже.'
        ) from exc

    if not isinstance(payload, dict):
        raise ServerUpdateError('GitHub вернул неожиданный ответ о последнем релизе.')

    tag = validate_release_tag(payload.get('tag_name'))
    release_name = str(payload.get('name') or tag).strip()[:160]
    published_at = str(payload.get('published_at') or '').strip()[:40]
    notes_html, notes_truncated = render_release_notes(payload.get('body'))
    return {
        'tag': tag,
        'stage': release_stage(tag),
        'name': release_name,
        'notes_html': notes_html,
        'notes_truncated': notes_truncated,
        'published_at': published_at,
        'published_label': format_release_date(published_at, config.get('APP_TIMEZONE', 'UTC')),
        'url': f'https://github.com/{repository}/releases/tag/{quote(tag, safe="")}',
    }


def format_release_day(value):
    """Format a ``date`` as "15 октября 2026"."""
    return f'{value.day} {_MONTHS_GENITIVE[value.month - 1]} {value.year}'


def _run_git(app_dir, *args):
    try:
        result = subprocess.run(
            [
                'git', '--no-replace-objects', '-c', f'safe.directory={app_dir}',
                '-C', str(app_dir), *args,
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def get_commit_details(config, commit):
    """Return the HEAD commit date and its GitHub link; each field may be empty."""
    details = {'date': '', 'date_label': '', 'url': ''}
    if not commit or not _COMMIT_RE.fullmatch(str(commit)):
        return details
    app_dir = Path(config.get('SERVER_UPDATE_APP_DIR') or '.').resolve()
    if (app_dir / '.git').exists():
        committed_at = _run_git(app_dir, 'log', '-1', '--format=%cI', 'HEAD')[:40]
        label = format_release_date(committed_at, config.get('APP_TIMEZONE', 'UTC'))
        if label:
            details.update(date=committed_at, date_label=label)
    try:
        repository = validate_repository(config.get('SERVER_UPDATE_REPOSITORY'))
    except ServerUpdateError:
        return details
    details['url'] = f'https://github.com/{repository}/commit/{commit}'
    return details


def get_last_successful_update(config):
    """Return the tag and time of the update recorded as successful, if any."""
    status = read_update_status(config)
    if not status or status.get('state') != 'success':
        return None
    try:
        tag = validate_release_tag(status.get('tag'))
    except ServerUpdateError:
        return None
    updated_at = str(status.get('updated_at') or '').strip()[:40]
    return {
        'tag': tag,
        'at': updated_at,
        'label': format_release_date(updated_at, config.get('APP_TIMEZONE', 'UTC')),
    }


def get_current_release(config):
    configured_version = str(config.get('APP_VERSION') or '').strip()
    app_dir = Path(config.get('SERVER_UPDATE_APP_DIR') or '.').resolve()
    if not (app_dir / '.git').exists() and configured_version:
        return {
            'label': configured_version,
            'tag': configured_version,
            'tags': [configured_version],
            'commit': None,
        }

    tags = [tag for tag in _run_git(app_dir, 'tag', '--points-at', 'HEAD').splitlines() if tag]
    commit = _run_git(app_dir, 'rev-parse', '--short=12', 'HEAD')
    if tags:
        return {'label': tags[0], 'tag': tags[0], 'tags': tags, 'commit': commit or None}
    if commit:
        return {'label': f'коммит {commit}', 'tag': None, 'tags': [], 'commit': commit}
    if configured_version:
        return {
            'label': configured_version,
            'tag': configured_version,
            'tags': [configured_version],
            'commit': None,
        }
    return {'label': 'не определена', 'tag': None, 'tags': [], 'commit': None}


def current_release_has_tag(current_release, tag):
    tags = current_release.get('tags')
    if isinstance(tags, list):
        return tag in tags
    return current_release.get('tag') == tag


_STAGE_LABELS = {
    'alpha': 'Альфа-версия',
    'beta': 'Бета-версия',
    'rc': 'Релиз-кандидат',
}


def release_stage(tag):
    """Describe a pre-release tag such as ``v0.0.14-alpha``; ``None`` for a stable one.

    Alpha releases are published as ordinary GitHub releases (so that
    ``/releases/latest`` returns them); the stage comes from the tag suffix.
    """
    match = re.fullmatch(
        r'v?\d+\.\d+\.\d+-([0-9A-Za-z.-]+)(?:\+[0-9A-Za-z.-]+)?',
        str(tag or '').strip(),
    )
    if not match:
        return None
    key = re.split(r'[.-]', match.group(1).lower(), maxsplit=1)[0]
    if key not in _STAGE_LABELS:
        return {'key': 'prerelease', 'label': 'Предварительная версия'}
    return {'key': key, 'label': _STAGE_LABELS[key]}


def _release_order(value):
    """Return a conservative comparable key for ordinary SemVer-like tags."""
    match = re.fullmatch(
        r'v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?',
        str(value or '').strip(),
    )
    if not match:
        return None
    major, minor, patch, prerelease = match.groups()
    return (int(major), int(minor), int(patch), prerelease is None)


def reconcile_update_status(status, current_release):
    """Do not let a failed older operation mask a successfully installed release."""
    if not isinstance(status, dict) or status.get('state') != 'failed':
        return status
    failed_order = _release_order(status.get('tag'))
    current_tags = current_release.get('tags')
    if not isinstance(current_tags, list):
        current_tags = [current_release.get('tag')]
    newer_tags = [tag for tag in current_tags if _release_order(tag)]
    if failed_order is None or not any(_release_order(tag) > failed_order for tag in newer_tags):
        return status
    reconciled = dict(status)
    reconciled.update({
        'state': 'success',
        'tag': str(current_release.get('label') or newer_tags[0]),
        'message': (
            'Установлена более новая версия; ошибка предыдущей операции сохранена '
            'только в журнале обновления.'
        ),
        'superseded': True,
    })
    return reconciled


def _status_path(config):
    value = str(config.get('SERVER_UPDATE_STATUS_PATH') or '').strip()
    if not value:
        app_dir = Path(config.get('SERVER_UPDATE_APP_DIR') or '.').resolve()
        return app_dir / 'instance' / 'server-update-status.json'
    path = Path(value)
    if not path.is_absolute():
        path = Path(config.get('SERVER_UPDATE_APP_DIR') or '.').resolve() / path
    return path.resolve()


def read_update_status(config):
    path = _status_path(config)
    try:
        if path.stat().st_size > 64 * 1024:
            return None
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _status_is_active(status, stale_minutes):
    if not status or status.get('state') not in _ACTIVE_STATES:
        return False
    raw_updated_at = str(status.get('updated_at') or '')
    try:
        updated_at = datetime.fromisoformat(raw_updated_at.replace('Z', '+00:00'))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (_utc_now() - updated_at).total_seconds() < stale_minutes * 60


def _ipc_socket_path(config):
    raw_path = str(config.get('SERVER_UPDATE_SOCKET') or '').strip()
    if not raw_path:
        raw_path = '/run/officium/updater.sock'
    path = Path(raw_path)
    if os.name != 'nt' and not path.is_absolute():
        raise ServerUpdateError('Путь к сокету обновления должен быть абсолютным.')
    return raw_path


def _ipc_request(config, action, **fields):
    """Call the fixed local updater protocol without spawning a privileged process."""
    timeout = max(1, min(int(config.get('SERVER_UPDATE_IPC_TIMEOUT_SECONDS', 8)), 30))
    if action == 'start_latest_update':
        # The runner checks GitHub, the Git remote and systemd-run before it answers.
        timeout = max(timeout, _START_IPC_TIMEOUT_SECONDS)
    request_payload = {
        'protocol': 1,
        'action': str(action),
        **fields,
    }
    encoded = json.dumps(
        request_payload,
        ensure_ascii=True,
        separators=(',', ':'),
    ).encode('ascii') + b'\n'
    if len(encoded) > 4096:
        raise ServerUpdateError('Запрос обновления превышает допустимый размер.')

    response = bytearray()
    if not hasattr(socket, 'AF_UNIX'):
        raise ServerUpdateError(_IPC_ERROR_MESSAGES['ipc_unavailable'])
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(_ipc_socket_path(config))
            client.sendall(encoded)
            while b'\n' not in response:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > _MAX_IPC_RESPONSE_BYTES:
                    raise ServerUpdateError(_IPC_ERROR_MESSAGES['protocol_error'])
    except socket.timeout as exc:
        raise ServerUpdateError(_IPC_ERROR_MESSAGES['ipc_timeout']) from exc
    except (OSError, ValueError) as exc:
        logger.warning('Updater IPC unavailable: %s', type(exc).__name__)
        raise ServerUpdateError(_IPC_ERROR_MESSAGES['ipc_unavailable']) from exc

    try:
        raw_line = bytes(response).split(b'\n', 1)[0]
        payload = json.loads(raw_line.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ServerUpdateError(_IPC_ERROR_MESSAGES['protocol_error']) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('ok'), bool):
        raise ServerUpdateError(_IPC_ERROR_MESSAGES['protocol_error'])
    if not payload['ok']:
        code = str(payload.get('code') or 'unexpected_failure')
        message = _IPC_ERROR_MESSAGES.get(code, _IPC_ERROR_MESSAGES['unexpected_failure'])
        operation_id = str(payload.get('operation_id') or '')
        logger.warning(
            'Updater rejected %s; code=%s operation_id=%s',
            action, code if code in _IPC_ERROR_MESSAGES else 'unknown', operation_id or '-',
        )
        suffix = f' Код операции: {operation_id}.' if operation_id else ''
        raise ServerUpdateError(f'{message}{suffix}')
    return payload


def update_is_active(config, status=None):
    stale_minutes = max(10, min(int(config.get('SERVER_UPDATE_STALE_MINUTES', 120)), 1440))
    return _status_is_active(status if status is not None else read_update_status(config), stale_minutes)


def check_update_readiness(config, *, integration=False):
    action = 'integration_check' if integration else 'check'
    return _ipc_request(config, action)


def get_update_status(config):
    """Read status through the authenticated IPC path, with a read-only file fallback."""
    if not config.get('SERVER_UPDATE_ENABLED'):
        return read_update_status(config)
    try:
        payload = _ipc_request(config, 'get_status')
    except ServerUpdateError:
        return read_update_status(config)
    status = payload.get('status')
    return status if isinstance(status, dict) else None


def request_server_update(config, requested_tag, requested_by):
    if not config.get('SERVER_UPDATE_ENABLED'):
        raise ServerUpdateError('Обновление сервера отключено в конфигурации.')

    tag = validate_release_tag(requested_tag)
    latest = fetch_latest_release(config)
    if tag != latest['tag']:
        raise ServerUpdateError(
            'Выбранный релиз уже не является последним. Обновите страницу и проверьте версию.'
        )

    current = get_current_release(config)
    if current_release_has_tag(current, tag):
        raise ServerUpdateError('На сервере уже установлена эта версия.')

    operation_id = f'upd-{uuid.uuid4().hex[:12]}'
    payload = _ipc_request(
        config,
        'start_latest_update',
        tag=tag,
        operation_id=operation_id,
        requested_by=str(requested_by),
    )
    latest['operation_id'] = str(payload.get('operation_id') or operation_id)
    return latest
