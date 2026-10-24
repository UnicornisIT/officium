"""Peak memory of bank statement parsing on synthetic files at the upload limit.

    python tests/performance/import_memory_profile.py [--json OUT] [SCENARIO ...]

Every scenario runs in a fresh interpreter so the peak resident set size
(ru_maxrss on Linux, PeakWorkingSetSize on Windows) belongs to that parse
alone. The baseline is the same interpreter after importing the application
without parsing anything; "parse" is the peak minus that baseline. All data
is synthetic.
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIMIT_BYTES = 10 * 1024 * 1024
MERCHANTS = ('PEREKRESTOK', 'MAGNIT', 'YANDEX GO', 'Аптека 36.6', 'Spotify', 'МТС', 'LENTA', 'AZS 24')


def _csv_line(index, income):
    day = index % 28 + 1
    merchant = 'Зарплата ООО Ромашка' if income else MERCHANTS[index % len(MERCHANTS)]
    amount = f'{"" if income else "-"}{index % 9000 + 100},{index % 100:02d}'
    category = 'Переводы' if income else 'Супермаркеты'
    return f'{day:02d}.07.2026;{merchant} операция {index};{amount};{category};*1234\n'


def build_csv(income_heavy, limit_bytes=LIMIT_BYTES):
    """A cp1251 CSV just under the upload limit."""
    header = 'Сбербанк Онлайн\nДата операции;Описание операции;Сумма операции;Категория;Карта\n'
    parts = [header]
    size = len(header.encode('cp1251'))
    index = 0
    while True:
        # income_heavy: 400 expenses, then incomes only, so the whole file is read.
        line = _csv_line(index, income=income_heavy and index >= 400)
        line_size = len(line.encode('cp1251'))
        if size + line_size > limit_bytes:
            break
        parts.append(line)
        size += line_size
        index += 1
    return ''.join(parts).encode('cp1251'), index


def build_xlsx(target_uncompressed=48 * 1024 * 1024, income_heavy=True):
    """An XLSX whose sheet XML is close to the 50 MiB uncompressed cap."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    header = ['Дата операции', 'Описание операции', 'Сумма операции', 'Категория']
    rows = []
    size = 0
    index = 0
    while size < target_uncompressed:
        if index == 0:
            values = header
        else:
            income = income_heavy and index > 400
            amount = f'{"" if income else "-"}{index % 9000 + 100},{index % 100:02d}'
            category = 'Переводы' if income else 'Покупки'
            values = [f'{index % 28 + 1:02d}.07.2026', f'{"Зарплата ООО Ромашка" if income else MERCHANTS[index % len(MERCHANTS)]} {index}', amount, category]
        cells = ''.join(
            f'<c r="{"ABCD"[col]}{index + 1}" t="inlineStr"><is><t>{value}</t></is></c>'
            for col, value in enumerate(values)
        )
        row = f'<row r="{index + 1}">{cells}</row>'
        rows.append(row)
        size += len(row.encode('utf-8'))
        index += 1
    sheet = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{ns}"><sheetData>{"".join(rows)}</sheetData></worksheet>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return buffer.getvalue(), index


def _pdf_escape(text):
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def build_pdf(pages=200, lines_per_page=60):
    """A text PDF at the page cap: one Helvetica content stream per page."""
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    font = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    page_ids = []
    pages_id_placeholder = len(objects) + 1 + pages * 2
    for page in range(pages):
        commands = ['BT /F1 8 Tf 20 800 Td 10 TL']
        for line in range(lines_per_page):
            index = page * lines_per_page + line
            commands.append(
                f'({_pdf_escape(f"{index % 28 + 1:02d}.07.2026 PEREKRESTOK {index} -{index % 900 + 100},00")}) \''
            )
        commands.append('ET')
        stream = '\n'.join(commands).encode('latin-1')
        content = add(b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream')
        page_ids.append(add(
            b'<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] '
            b'/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>'
            % (pages_id_placeholder, font, content)
        ))
    kids = b' '.join(b'%d 0 R' % page_id for page_id in page_ids)
    pages_id = add(b'<< /Type /Pages /Kids [%s] /Count %d >>' % (kids, pages))
    assert pages_id == pages_id_placeholder
    catalog = add(b'<< /Type /Catalog /Pages %d 0 R >>' % pages_id)

    output = io.BytesIO()
    output.write(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(b'%d 0 obj\n' % number + body + b'\nendobj\n')
    xref = output.tell()
    output.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1))
    for offset in offsets:
        output.write(b'%010d 00000 n \n' % offset)
    output.write(b'trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, catalog, xref))
    return output.getvalue(), pages * lines_per_page


SCENARIOS = {
    'csv_expenses': ('statement.csv', lambda: build_csv(income_heavy=False)),
    'csv_income_heavy': ('statement.csv', lambda: build_csv(income_heavy=True)),
    'xlsx_income_heavy': ('statement.xlsx', lambda: build_xlsx(income_heavy=True)),
    'pdf_200_pages': ('statement.pdf', build_pdf),
}


def peak_rss_bytes():
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL('kernel32')
        psapi = ctypes.WinDLL('psapi')
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return counters.PeakWorkingSetSize
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def run_child(scenario, file_path):
    """Child process: import the app, then parse the prepared file once."""
    sys.path.insert(0, str(ROOT))
    from app.services.bank_statement_import_service import parse_bank_statement
    from config import Config
    from app import create_app

    create_app()  # load the same modules the web process has before measuring

    baseline = peak_rss_bytes()
    data = Path(file_path).read_bytes()
    filename = SCENARIOS[scenario][0]
    started = time.perf_counter()
    outcome = 'ok'
    rows = 0
    try:
        # The limits the import route applies (IMPORT_MAX_ROWS, IMPORT_MAX_SOURCE_ROWS).
        limits = {
            'max_rows': getattr(Config, 'IMPORT_MAX_ROWS', None),
            'max_source_rows': getattr(Config, 'IMPORT_MAX_SOURCE_ROWS', None),
        }
        if os.environ.get('PROFILE_WITHOUT_LIMITS') == '1':
            limits = {}
        try:
            result = parse_bank_statement(data, filename, **limits)
        except TypeError:
            # Releases before the streaming parser had no limit arguments.
            result = parse_bank_statement(data, filename)
        rows = len(result.rows)
    except ValueError as exc:
        outcome = f'rejected: {exc}'
    elapsed = time.perf_counter() - started
    print(json.dumps({
        'scenario': scenario,
        'file_bytes': len(data),
        'rows': rows,
        'outcome': outcome,
        'seconds': round(elapsed, 2),
        'baseline_rss_mib': round(baseline / 1048576, 1),
        'peak_rss_mib': round(peak_rss_bytes() / 1048576, 1),
        'parse_peak_mib': round((peak_rss_bytes() - baseline) / 1048576, 1),
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('scenarios', nargs='*', default=list(SCENARIOS))
    parser.add_argument('--json', help='write the results to this file')
    parser.add_argument('--child', nargs=2, metavar=('SCENARIO', 'FILE'), help=argparse.SUPPRESS)
    parser.add_argument('--workdir', default=None, help='where the synthetic files are written')
    args = parser.parse_args()
    if args.child:
        run_child(*args.child)
        return 0

    import tempfile
    workdir = Path(args.workdir or tempfile.mkdtemp(prefix='officium-import-profile-'))
    workdir.mkdir(parents=True, exist_ok=True)
    results = []
    for scenario in args.scenarios:
        filename, builder = SCENARIOS[scenario]
        data, generated_rows = builder()
        file_path = workdir / f'{scenario}{Path(filename).suffix}'
        file_path.write_bytes(data)
        environment = dict(os.environ, OFFICIUM_ENV='development', DB_ENGINE='sqlite', PYTHONIOENCODING='utf-8')
        completed = subprocess.run(
            [sys.executable, __file__, '--child', scenario, str(file_path)],
            capture_output=True, text=True, encoding='utf-8', env=environment, check=False,
        )
        lines = [line for line in completed.stdout.splitlines() if line.startswith('{')]
        if completed.returncode != 0 or not lines:
            print(completed.stderr, file=sys.stderr)
            raise SystemExit(f'{scenario} failed')
        result = json.loads(lines[-1])
        result['generated_rows'] = generated_rows
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))
        file_path.unlink()
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
