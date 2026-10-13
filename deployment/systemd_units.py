#!/usr/bin/python3
"""Read Officium's systemd unit and drop-ins without guessing.

    systemd_units.py params < `systemctl cat officium.service`
        Print HOST, PORT, THREADS, TRUSTED_PROXY, TRUSTED_PROXY_HEADERS,
        APP_USER, APP_GROUP and VENV_DIR of the *effective* ExecStart
        (drop-ins included) as KEY=VALUE lines; unknown values are omitted.
    systemd_units.py dropins NEW_UNIT DROPIN_DIR APP_ROOT
        For every *.conf in DROPIN_DIR that sets ExecStart, print
        "remove<TAB>path<TAB>reason" or "keep<TAB>path<TAB>reason".

A drop-in is removed only when it sets nothing but ExecStart and that
ExecStart either runs exactly what the new unit runs or points at a
program that cannot work any more (a venv console script, or a path that
does not exist). Everything else is kept and reported.
"""

import os
import re
import shlex
import sys
from pathlib import Path, PurePosixPath


SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')


def logical_lines(text):
    """Unit file lines with backslash continuations joined and comments dropped."""
    pending = ''
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not pending and line.lstrip().startswith(('#', ';')):
            continue
        if line.endswith('\\'):
            pending += line[:-1] + ' '
            continue
        yield pending + line
        pending = ''
    if pending:
        yield pending


def service_settings(text):
    """(key, value) pairs of the [Service] section, in order."""
    section = ''
    for line in logical_lines(text):
        match = SECTION_RE.match(line)
        if match:
            section = match.group(1)
            continue
        if section != 'Service' or '=' not in line:
            continue
        key, value = line.split('=', 1)
        yield key.strip(), value.strip()


def split_systemctl_cat(text):
    """`systemctl cat` prints each file after a "# /path" header line."""
    files = []
    for line in text.splitlines():
        if re.match(r'^# /\S+$', line):
            files.append([])
        elif files:
            files[-1].append(line)
    return ['\n'.join(lines) for lines in files] or [text]


def effective_exec_start(texts):
    """The ExecStart command lines left after applying files in order."""
    commands = []
    for text in texts:
        for key, value in service_settings(text):
            if key != 'ExecStart':
                continue
            if value == '':
                commands = []
            else:
                commands.append(value)
    return commands


def exec_argv(command):
    # Leading special-executable prefixes (-, @, :, +, !) do not change argv.
    return shlex.split(command.lstrip('-@:+!'))


def waitress_params(argv):
    """Waitress settings from either `.../waitress-serve ...` or `python -m waitress ...`."""
    if not argv:
        return {}
    params = {}
    program = argv[0]
    if program.endswith('/waitress-serve'):
        params['VENV_DIR'] = str(PurePosixPath(program).parent.parent)
        options = argv[1:]
    elif program.endswith('/bin/python') and argv[1:3] == ['-m', 'waitress']:
        params['VENV_DIR'] = str(PurePosixPath(program).parent.parent)
        options = argv[3:]
    else:
        return {}
    for option in options:
        name, _separator, value = option.partition('=')
        if name == '--listen':
            host, _colon, port = value.rpartition(':')
            params['HOST'] = host.strip('[]')
            params['PORT'] = port
        elif name == '--threads':
            params['THREADS'] = value
        elif name == '--trusted-proxy':
            params['TRUSTED_PROXY'] = value
        elif name == '--trusted-proxy-headers':
            params['TRUSTED_PROXY_HEADERS'] = value
    return params


def unit_params(systemctl_cat_output):
    texts = split_systemctl_cat(systemctl_cat_output)
    params = {}
    for text in texts:
        for key, value in service_settings(text):
            if key == 'User':
                params['APP_USER'] = value
            elif key == 'Group':
                params['APP_GROUP'] = value
    commands = effective_exec_start(texts)
    if commands:
        params.update(waitress_params(exec_argv(commands[-1])))
    return params


def classify_dropin(dropin_text, new_unit_text, app_root, path_exists=os.path.exists):
    """('remove' | 'keep' | None, reason) for one drop-in file."""
    settings = list(service_settings(dropin_text))
    commands = [value for key, value in settings if key == 'ExecStart' and value]
    if not any(key == 'ExecStart' for key, _value in settings):
        return None, 'does not set ExecStart'
    other = sorted({key for key, _value in settings if key != 'ExecStart'})
    new_commands = effective_exec_start([new_unit_text])
    if not commands:
        return ('keep', 'only clears ExecStart') if other else ('remove', 'only clears ExecStart')
    argv = exec_argv(commands[-1])
    if new_commands and argv == exec_argv(new_commands[-1]):
        reason = 'runs exactly what the new unit runs'
    elif argv and argv[0].endswith('/waitress-serve'):
        reason = f'runs the venv console script {argv[0]}, which breaks when a release moves'
    elif argv and not path_exists(argv[0]):
        reason = f'runs {argv[0]}, which does not exist'
    elif argv and argv[0].startswith(app_root.rstrip('/') + '/') and \
            not argv[0].startswith(app_root.rstrip('/') + '/current/'):
        reason = f'runs {argv[0]} from the old checkout'
    else:
        return 'keep', f'overrides ExecStart with a different command: {commands[-1]}'
    if other:
        return 'keep', f'{reason}, but also sets {", ".join(other)}; edit it by hand'
    return 'remove', reason


def main(argv):
    if len(argv) == 2 and argv[1] == 'params':
        for key, value in sorted(unit_params(sys.stdin.read()).items()):
            print(f'{key}={value}')
        return 0
    if len(argv) == 5 and argv[1] == 'dropins':
        new_unit = Path(argv[2]).read_text(encoding='utf-8')
        dropin_dir = Path(argv[3])
        if not dropin_dir.is_dir():
            return 0
        for path in sorted(dropin_dir.glob('*.conf')):
            decision, reason = classify_dropin(path.read_text(encoding='utf-8'), new_unit, argv[4])
            if decision:
                print(f'{decision}\t{path}\t{reason}')
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
