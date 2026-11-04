"""Управление службами Telegram из проверенного managed-релиза."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def _run(command, label, *, cwd=None, check=True):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=180, check=False)
    if check and result.returncode:
        raise RuntimeError(f'{label} failed.')
    return result


def sync(app_root, app_user, app_group, service_name='officium', venv_name='venv', *, unit_dir=Path('/etc/systemd/system')):
    root = Path(app_root)
    if not root.is_absolute() or not re.fullmatch(r'[A-Za-z0-9_./:{}~-]+', root.as_posix()):
        raise ValueError('Invalid application root.')
    if not all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}', value) for value in (app_user, app_group, service_name)):
        raise ValueError('Invalid service account or name.')
    if venv_name not in ('venv', '.venv'):
        raise ValueError('Invalid virtual environment name.')
    current = root / 'current'
    python = current / venv_name / 'bin/python'
    systemctl = shutil.which('systemctl')
    runuser = shutil.which('runuser')
    if not systemctl or not runuser:
        raise RuntimeError('systemctl and runuser are required.')
    cli = [runuser, '-u', app_user, '--', str(python), '-m', 'flask', '--app', 'run.py', 'telegram-setup']
    state = json.loads(_run([*cli, '--dry-run'], 'Telegram configuration', cwd=current).stdout)
    if (not isinstance(state, dict) or type(state.get('bot_enabled')) is not bool
            or type(state.get('monthly_reminders')) is not bool
            or state.get('receive_mode') not in ('polling', 'webhook')):
        raise ValueError('Invalid Telegram service configuration.')
    poll = service_name + '-telegram-polling.service'
    timer = service_name + '-monthly-reminders.timer'
    monthly = service_name + '-monthly-reminders.service'
    # Сначала освобождаем токен; webhook и polling не должны работать вместе.
    _run([systemctl, 'stop', poll, timer, monthly], 'Stop Telegram services', check=False)
    _run(cli, 'Telegram receive setup', cwd=current)
    substitutions = {
        '@@APP_ROOT@@': root.as_posix(), '@@APP_USER@@': app_user, '@@APP_GROUP@@': app_group,
        '@@VENV_DIR@@': (current / venv_name).as_posix(), '@@SERVICE_NAME@@': service_name,
    }
    for source, target in (
        ('officium-telegram-polling.service', poll),
        ('officium-monthly-reminders.service', monthly),
        ('officium-monthly-reminders.timer', timer),
    ):
        content = (current / 'deployment' / source).read_text(encoding='utf-8')
        for key, value in substitutions.items():
            content = content.replace(key, value)
        if '@@' in content:
            raise ValueError('Unresolved service template parameter.')
        path = unit_dir / target
        if path.is_symlink():
            raise ValueError('Managed Telegram unit must not be a symlink.')
        path.write_text(content, encoding='utf-8')
        path.chmod(0o644)
    verify = shutil.which('systemd-analyze')
    if not verify:
        raise RuntimeError('systemd-analyze is required.')
    _run([verify, 'verify', *(str(unit_dir / name) for name in (poll, monthly, timer))], 'Verify Telegram units')
    _run([systemctl, 'daemon-reload'], 'Reload Telegram units')
    for name, enabled in ((poll, state['bot_enabled'] and state['receive_mode'] == 'polling'),
                          (timer, state['monthly_reminders'])):
        _run([systemctl, 'enable' if enabled else 'disable', '--now', name], 'Activate Telegram service')
        if enabled:
            _run([systemctl, 'is-active', '--quiet', name], 'Check Telegram service')
    marker = root / 'shared' / '.telegram-services-release'
    temporary = marker.with_name(f'.telegram-services-release-{os.getpid()}')
    try:
        temporary.write_text(str(current.resolve(strict=True)) + '\n', encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', required=True)
    parser.add_argument('--app-user', required=True)
    parser.add_argument('--app-group', required=True)
    parser.add_argument('--service-name', default='officium')
    parser.add_argument('--venv-name', default='venv')
    args = parser.parse_args()
    try:
        state = sync(**vars(args))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        print('Telegram service configuration failed; check the bot, proxy and service settings.', file=sys.stderr)
        return 1
    print(json.dumps(state, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
