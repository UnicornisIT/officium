import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


class ServerUpdateError(RuntimeError):
    """A safe, user-facing server update error."""


_REPOSITORY_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
_RELEASE_TAG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$')
_ACTIVE_STATES = {'queued', 'running', 'backing_up', 'installing', 'migrating', 'restarting'}


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_now():
    return _utc_now().isoformat().replace('+00:00', 'Z')


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
    token = str(config.get('SERVER_UPDATE_GITHUB_TOKEN') or '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'

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
    return {
        'tag': tag,
        'name': release_name,
        'published_at': published_at,
        'url': f'https://github.com/{repository}/releases/tag/{quote(tag, safe="")}',
    }


def get_current_release(config):
    configured_version = str(config.get('APP_VERSION') or '').strip()
    if configured_version:
        return {
            'label': configured_version,
            'tag': configured_version,
            'tags': [configured_version],
            'commit': None,
        }

    app_dir = Path(config.get('SERVER_UPDATE_APP_DIR') or '.').resolve()

    def run_git(*args):
        try:
            result = subprocess.run(
                ['git', '-C', str(app_dir), *args],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ''
        return result.stdout.strip() if result.returncode == 0 else ''

    tags = [tag for tag in run_git('tag', '--points-at', 'HEAD').splitlines() if tag]
    commit = run_git('rev-parse', '--short=12', 'HEAD')
    if tags:
        return {'label': tags[0], 'tag': tags[0], 'tags': tags, 'commit': commit or None}
    if commit:
        return {'label': f'коммит {commit}', 'tag': None, 'tags': [], 'commit': commit}
    return {'label': 'не определена', 'tag': None, 'tags': [], 'commit': None}


def current_release_has_tag(current_release, tag):
    tags = current_release.get('tags')
    if isinstance(tags, list):
        return tag in tags
    return current_release.get('tag') == tag


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


def _write_update_status(config, payload):
    path = _status_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    try:
        temporary.write_text(data, encoding='utf-8')
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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


def update_is_active(config, status=None):
    stale_minutes = max(10, min(int(config.get('SERVER_UPDATE_STALE_MINUTES', 120)), 1440))
    return _status_is_active(status if status is not None else read_update_status(config), stale_minutes)


def _validated_helper_command(config):
    helper_value = str(config.get('SERVER_UPDATE_HELPER') or '').strip()
    helper = Path(helper_value)
    if not helper_value or not helper.is_absolute() or not helper.is_file():
        raise ServerUpdateError('Серверный помощник обновления не установлен или не настроен.')

    if os.name != 'nt':
        if not os.access(helper, os.X_OK):
            raise ServerUpdateError('Серверный помощник обновления не является исполняемым.')
        stat = helper.stat()
        require_root_owner = bool(config.get('SERVER_UPDATE_REQUIRE_ROOT_OWNED_HELPER', True))
        if require_root_owner and stat.st_uid != 0:
            raise ServerUpdateError('Серверный помощник обновления должен принадлежать root.')
        if stat.st_mode & 0o022:
            raise ServerUpdateError('Серверный помощник обновления доступен для небезопасной записи.')

    command = [str(helper)]
    if config.get('SERVER_UPDATE_USE_SUDO'):
        sudo_value = str(config.get('SERVER_UPDATE_SUDO_PATH') or '/usr/bin/sudo').strip()
        sudo = Path(sudo_value)
        if not sudo.is_absolute() or not sudo.is_file():
            raise ServerUpdateError('Для запуска обновления не найден sudo.')
        command = [str(sudo), '-n', str(helper)]
    return command


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

    command = _validated_helper_command(config)
    status_path = _status_path(config)
    lock_path = status_path.with_name(f'{status_path.name}.request.lock')
    status_path.parent.mkdir(parents=True, exist_ok=True)
    stale_minutes = max(10, min(int(config.get('SERVER_UPDATE_STALE_MINUTES', 120)), 1440))
    lock_fd = None
    for attempt in range(2):
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError as exc:
            try:
                lock_age_seconds = _utc_now().timestamp() - lock_path.stat().st_mtime
            except OSError:
                lock_age_seconds = 0
            if attempt == 0 and lock_age_seconds >= stale_minutes * 60:
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass
            raise ServerUpdateError('Другой запрос обновления уже обрабатывается.') from exc

    if lock_fd is None:
        raise ServerUpdateError('Не удалось заблокировать параллельный запуск обновления.')

    try:
        with os.fdopen(lock_fd, 'w', encoding='utf-8') as lock_file:
            lock_file.write(_iso_now())

        existing_status = read_update_status(config)
        if update_is_active(config, existing_status):
            raise ServerUpdateError('Обновление уже запущено. Дождитесь его завершения.')

        queued_status = {
            'state': 'queued',
            'tag': tag,
            'message': 'Запрос принят сервером и ожидает запуска.',
            'requested_by': str(requested_by),
            'requested_at': _iso_now(),
            'updated_at': _iso_now(),
        }
        _write_update_status(config, queued_status)

        timeout = max(3, min(int(config.get('SERVER_UPDATE_COMMAND_TIMEOUT_SECONDS', 15)), 60))
        try:
            result = subprocess.run(
                [*command, tag],
                cwd=str(Path(config.get('SERVER_UPDATE_APP_DIR') or '.').resolve()),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failed_status = dict(queued_status)
            failed_status.update({
                'state': 'failed',
                'message': 'Не удалось передать запрос серверному помощнику.',
                'updated_at': _iso_now(),
            })
            _write_update_status(config, failed_status)
            raise ServerUpdateError(failed_status['message']) from exc

        if result.returncode != 0:
            failed_status = dict(queued_status)
            failed_status.update({
                'state': 'failed',
                'message': 'Серверный помощник отклонил запуск обновления.',
                'updated_at': _iso_now(),
            })
            _write_update_status(config, failed_status)
            raise ServerUpdateError(failed_status['message'])
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    return latest
