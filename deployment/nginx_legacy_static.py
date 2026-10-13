#!/usr/bin/python3
"""Find (and optionally disable) nginx locations that serve files from APP_ROOT.

Officium's static files are served by Flask through the proxy. An older
`location /static/ { alias /opt/officium/static/; }` breaks every page's CSS
once the legacy checkout is moved away, and an alias into
/opt/officium/current/static is refused (403) because www-data cannot enter
the root:officium 0750 application tree.

    nginx_legacy_static.py check APP_ROOT [CONFIG_DIR]
        Print "file:line: directive" for each alias/root under APP_ROOT.
        Exit 0 when there is none, 1 when something was found.
    nginx_legacy_static.py disable APP_ROOT FILE...
        Comment out every location block of FILE that contains such a
        directive. The caller makes the backup and runs `nginx -t`.
"""

import os
import re
import sys
from pathlib import Path


DIRECTIVE_RE = re.compile(r'^\s*(alias|root)\s+("?)([^";\s]+)\2\s*$')
MARKER = '# officium: disabled, static files are served by the application: '


def _strip_comment(line):
    return line.split('#', 1)[0]


def offending_directives(text, app_root):
    """(line_number, directive) for every alias/root that points into app_root."""
    root = app_root.rstrip('/')
    found = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        for statement in re.split(r'[;{}]', _strip_comment(raw_line)):
            match = DIRECTIVE_RE.match(statement)
            if not match:
                continue
            target = match.group(3)
            if target == root or target.startswith(root + '/'):
                found.append((number, statement.strip() + ';'))
    return found


def config_files(config_dir):
    """Every regular config file once, following the sites-enabled symlinks."""
    seen = set()
    for directory, _dirnames, filenames in os.walk(config_dir, followlinks=True):
        for name in sorted(filenames):
            path = Path(directory) / name
            try:
                real = path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if real in seen or not real.is_file():
                continue
            seen.add(real)
            yield real


def check(app_root, config_dir='/etc/nginx'):
    findings = []
    for path in config_files(config_dir):
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend((path, number, directive) for number, directive in offending_directives(text, app_root))
    return findings


def disable_locations(text, app_root):
    """Comment out location blocks that contain an alias/root into app_root."""
    lines = text.splitlines(keepends=True)
    offending = {number - 1 for number, _directive in offending_directives(text, app_root)}
    output = list(lines)
    index = 0
    while index < len(lines):
        if not re.match(r'^\s*location\b', _strip_comment(lines[index])):
            index += 1
            continue
        depth = 0
        end = index
        opened = False
        for end in range(index, len(lines)):
            code = _strip_comment(lines[end])
            depth += code.count('{') - code.count('}')
            opened = opened or '{' in code
            if opened and depth <= 0:
                break
        block = range(index, end + 1)
        if offending.intersection(block):
            for line_index in block:
                if lines[line_index].strip():
                    output[line_index] = MARKER + lines[line_index].lstrip()
        index = end + 1
    return ''.join(output)


def main(argv):
    if len(argv) >= 3 and argv[1] == 'check':
        app_root = argv[2]
        config_dir = argv[3] if len(argv) > 3 else '/etc/nginx'
        findings = check(app_root, config_dir)
        for path, number, directive in findings:
            print(f'{path}:{number}: {directive}')
        return 1 if findings else 0
    if len(argv) >= 4 and argv[1] == 'disable':
        app_root = argv[2]
        for name in argv[3:]:
            path = Path(name)
            text = path.read_text(encoding='utf-8')
            updated = disable_locations(text, app_root)
            if updated != text:
                path.write_text(updated, encoding='utf-8')
                print(f'disabled legacy static location in {path}')
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
