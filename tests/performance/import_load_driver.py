"""Reproducible resource run of a live Officium server (synthetic data only).

    # 1. In the application environment (release venv, its .env): create or
    #    reuse a QA user and print a signed session for it.
    python tests/performance/import_load_driver.py mint --telegram-id -880000000001 > session.json

    # 2. Drive the running server and sample its memory, CPU and disk.
    python tests/performance/import_load_driver.py run --base-url http://127.0.0.1:5000 \
        --session session.json --pid <waitress pid> | --cgroup /sys/fs/cgroup/<slice> \
        --storage-dir <instance>/bank_imports --out results.json

    # 3. Remove the QA user and everything it imported.
    python tests/performance/import_load_driver.py cleanup --telegram-id -880000000001

Every scenario reports wall time, HTTP statuses, the peak resident memory
of the server (process RSS or cgroup memory.current, sampled every 50 ms),
CPU seconds used by the server and the change of free disk space. Nothing
here is a production measurement unless it ran on the production machine.
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import import_memory_profile as samples  # noqa: E402

MIB = 1024 * 1024
UI_PAGES = ('/', '/expenses', '/incomes', '/finance', '/financial-plan', '/goals', '/mortgages',
            '/archive', '/profile', '/documentation', '/expenses/import')


# ---------------------------------------------------------------- application side

def _application():
    sys.path.insert(0, os.getcwd())
    sys.path.insert(0, str(ROOT))
    from run import app  # noqa: WPS433  (the release under test)
    return app


def mint(telegram_id):
    app = _application()
    from flask import session
    from flask_wtf.csrf import generate_csrf

    from app.models import User
    from extensions import db

    with app.app_context():
        user = User.query.filter_by(telegram_id=telegram_id).first()
        if user is None:
            user = User(telegram_id=telegram_id, username='qa-resource-run', first_name='QA', role='user')
            db.session.add(user)
            db.session.commit()
        user_id = user.id
    with app.test_request_context('/'):
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
        csrf = generate_csrf()
        cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
    print(json.dumps({
        'cookie_name': app.config.get('SESSION_COOKIE_NAME', 'session'),
        'cookie': cookie,
        'csrf': csrf,
        'user_id': user_id,
        'instance_path': app.instance_path,
    }))


def cleanup(telegram_id):
    app = _application()
    from app.models import Expense, User
    from extensions import db

    with app.app_context():
        user = User.query.filter_by(telegram_id=telegram_id).first()
        if user is None:
            print('nothing to clean up')
            return
        removed = Expense.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
        print(f'removed QA user {user.id} and {removed} expenses')


# ---------------------------------------------------------------- sampling

class Sampler(threading.Thread):
    """Polls the server's memory and CPU every 50 ms."""

    def __init__(self, pid=None, cgroup=None):
        super().__init__(daemon=True)
        self.pid = pid
        self.cgroup = Path(cgroup) if cgroup else None
        self.peak = 0
        self.lock = threading.Lock()
        self.running = True
        self.process = None
        if pid and not Path(f'/proc/{pid}').exists():
            import psutil  # Windows/macOS developer machines only
            self.process = psutil.Process(pid)

    def memory(self):
        if self.cgroup:
            return int((self.cgroup / 'memory.current').read_text())
        if self.process is not None:
            return self.process.memory_info().rss
        for line in Path(f'/proc/{self.pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
        return 0

    def cpu_seconds(self):
        if self.cgroup:
            for line in (self.cgroup / 'cpu.stat').read_text().splitlines():
                if line.startswith('usage_usec'):
                    return int(line.split()[1]) / 1e6
        if self.process is not None:
            times = self.process.cpu_times()
            return times.user + times.system
        fields = Path(f'/proc/{self.pid}/stat').read_text().rsplit(')', 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')

    def reset_peak(self):
        with self.lock:
            self.peak = self.memory()

    def run(self):
        while self.running:
            try:
                value = self.memory()
            except (OSError, ValueError):
                value = 0
            with self.lock:
                self.peak = max(self.peak, value)
            time.sleep(0.05)


# ---------------------------------------------------------------- HTTP side

class Client:
    """requests with the session cookie sent by hand (it is Secure in production)."""

    def __init__(self, base_url, session):
        import requests
        self.http = requests.Session()
        self.base_url = base_url.rstrip('/')
        self.cookie_name = session['cookie_name']
        self.cookie = session['cookie']
        self.csrf = session['csrf']
        self.lock = threading.Lock()

    def request(self, method, path, **kwargs):
        headers = kwargs.pop('headers', {})
        with self.lock:
            headers['Cookie'] = f'{self.cookie_name}={self.cookie}'
        response = self.http.request(method, self.base_url + path, headers=headers,
                                     allow_redirects=False, timeout=180, **kwargs)
        match = re.search(rf'{re.escape(self.cookie_name)}=([^;]+)', response.headers.get('Set-Cookie', ''))
        if match:
            with self.lock:
                self.cookie = match.group(1)
        return response

    def upload(self, data, filename):
        return self.request('POST', '/expenses/import', data={'csrf_token': self.csrf},
                            files={'statement_file': (filename, io.BytesIO(data))})

    def confirm_defaults(self, preview_html):
        """Submit the preview form as the browser would, with its default choices."""
        form = {'csrf_token': self.csrf}
        form_html = preview_html[preview_html.index('confirm'):] if 'confirm' in preview_html else preview_html
        for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form_html):
            if name != 'csrf_token':
                form.setdefault(name, _unescape(value))
        for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', form_html, re.S):
            selected = re.search(r'<option value="([^"]*)"[^>]*selected', body) or \
                re.search(r'<option value="([^"]*)"', body)
            if selected:
                form[name] = _unescape(selected.group(1))
        for name, value in re.findall(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', form_html, re.S):
            form[name] = _unescape(value)
        return self.request('POST', '/expenses/import/confirm', data=form)


def _unescape(value):
    import html
    return html.unescape(value)


def _imported_count(response):
    location = response.headers.get('Location', '')
    message = parse_qs(urlsplit(location).query).get('success', [''])[0]
    match = re.search(r'Импортировано расходов: (\d+)', message)
    return int(match.group(1)) if match else None


def csv_statement(expenses, incomes=0, start=0):
    lines = [samples._csv_line(index, income=False) for index in range(start, start + expenses)]
    lines += [samples._csv_line(index, income=True) for index in range(incomes)]
    header = 'Сбербанк Онлайн\nДата операции;Описание операции;Сумма операции;Категория;Карта\n'
    return (header + ''.join(lines)).encode('cp1251')


# ---------------------------------------------------------------- scenarios

def run(args):
    session = json.loads(Path(args.session).read_text(encoding='utf-8'))
    client = Client(args.base_url, session)
    sampler = Sampler(pid=args.pid, cgroup=args.cgroup)
    sampler.start()
    storage_dir = Path(args.storage_dir) if args.storage_dir else Path(session['instance_path']) / 'bank_imports'
    disk_probe = storage_dir if storage_dir.exists() else storage_dir.parent
    results = []

    def scenario(name, action):
        time.sleep(0.5)
        sampler.reset_peak()
        before_rss = sampler.memory()
        before_cpu = sampler.cpu_seconds()
        before_free = shutil.disk_usage(disk_probe).free
        started = time.perf_counter()
        error = None
        try:
            detail = action()
        except Exception as exc:  # recorded, the run continues
            detail = {}
            error = f'{type(exc).__name__}: {exc}'
        elapsed = time.perf_counter() - started
        time.sleep(0.2)
        result = {
            'scenario': name,
            'seconds': round(elapsed, 2),
            'rss_before_mib': round(before_rss / MIB, 1),
            'rss_peak_mib': round(sampler.peak / MIB, 1),
            'rss_after_mib': round(sampler.memory() / MIB, 1),
            'cpu_seconds': round(sampler.cpu_seconds() - before_cpu, 2),
            'disk_free_delta_mib': round((shutil.disk_usage(disk_probe).free - before_free) / MIB, 2),
            'import_storage_files': len(list(storage_dir.iterdir())) if storage_dir.exists() else 0,
            **detail,
        }
        if error:
            result['error'] = error
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    def ui_sequential():
        statuses = {}
        for _round in range(5):
            for page in UI_PAGES:
                code = client.request('GET', page).status_code
                statuses[code] = statuses.get(code, 0) + 1
        return {'requests': sum(statuses.values()), 'statuses': statuses}

    def ui_parallel():
        statuses = {}
        lock = threading.Lock()

        def worker():
            for page in UI_PAGES * 2:
                code = client.request('GET', page).status_code
                with lock:
                    statuses[code] = statuses.get(code, 0) + 1

        threads = [threading.Thread(target=worker) for _index in range(args.parallel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return {'requests': sum(statuses.values()), 'statuses': statuses, 'clients': args.parallel}

    def import_and_confirm(data, filename):
        preview = client.upload(data, filename)
        detail = {'preview_status': preview.status_code, 'file_kib': round(len(data) / 1024, 1)}
        if preview.status_code == 200 and 'import_token' in preview.text:
            confirm = client.confirm_defaults(preview.text)
            detail.update(confirm_status=confirm.status_code, imported=_imported_count(confirm))
        return detail

    csv_500 = csv_statement(500)
    scenario('ui_sequential_55_requests', ui_sequential)
    scenario('ui_parallel_requests', ui_parallel)
    scenario('import_csv_500_rows', lambda: import_and_confirm(csv_500, 'synthetic.csv'))
    scenario('reimport_same_csv_duplicates_skipped', lambda: import_and_confirm(csv_500, 'synthetic.csv'))
    scenario('import_csv_19k_rows_mostly_income', lambda: import_and_confirm(
        csv_statement(300, incomes=19000, start=1000), 'synthetic.csv'))
    xlsx, _rows = samples.build_xlsx(target_uncompressed=4 * MIB, income_heavy=True)
    scenario('import_xlsx_4mib_sheet', lambda: import_and_confirm(xlsx, 'synthetic.xlsx'))
    pdf_small, _rows = samples.build_pdf(pages=8)
    scenario('import_pdf_8_pages', lambda: import_and_confirm(pdf_small, 'synthetic.pdf'))
    pdf_cap, _rows = samples.build_pdf(pages=200)
    scenario('import_pdf_200_pages_rejected', lambda: import_and_confirm(pdf_cap, 'synthetic.pdf'))
    # Just under the upload limit once multipart overhead is added.
    worst_csv, _rows = samples.build_csv(income_heavy=True, limit_bytes=int(9.5 * MIB))
    scenario('import_csv_9_5mib_rejected_by_row_limit', lambda: import_and_confirm(worst_csv, 'synthetic.csv'))
    worst_xlsx, _rows = samples.build_xlsx(income_heavy=True)
    scenario('import_xlsx_48mib_sheet_rejected', lambda: import_and_confirm(worst_xlsx, 'synthetic.xlsx'))
    scenario('upload_over_10mib_413', lambda: {'status': client.upload(b'0' * (11 * MIB), 'big.csv').status_code})

    def two_heavy_uploads():
        statuses = []

        def worker():
            statuses.append(client.upload(csv_statement(400, incomes=15000), 'synthetic.csv').status_code)

        threads = [threading.Thread(target=worker) for _index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return {'statuses': sorted(statuses)}

    scenario('two_parallel_heavy_previews', two_heavy_uploads)

    sampler.running = False
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('mint', 'cleanup'):
        command = commands.add_parser(name)
        command.add_argument('--telegram-id', type=int, default=-880000000001)
    command = commands.add_parser('run')
    command.add_argument('--base-url', default='http://127.0.0.1:5000')
    command.add_argument('--session', required=True)
    command.add_argument('--pid', type=int)
    command.add_argument('--cgroup')
    command.add_argument('--storage-dir')
    command.add_argument('--parallel', type=int, default=4)
    command.add_argument('--out')
    args = parser.parse_args()
    if args.command == 'mint':
        mint(args.telegram_id)
    elif args.command == 'cleanup':
        cleanup(args.telegram_id)
    else:
        if not (args.pid or args.cgroup):
            parser.error('run needs --pid or --cgroup')
        run(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
