"""Real MySQL backup/restore gate; run as root in disposable CI/staging only."""

import os
import runpy
import subprocess
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pymysql


required = (
    'OFFICIUM_MYSQL_HOST', 'OFFICIUM_MYSQL_PORT', 'OFFICIUM_MYSQL_USER',
    'OFFICIUM_MYSQL_PASSWORD', 'OFFICIUM_MYSQL_DATABASE',
    'OFFICIUM_MYSQL_RESTORE_DATABASE', 'OFFICIUM_APP_USER',
)
missing = [key for key in required if not os.environ.get(key)]
if missing:
    raise SystemExit(f'Missing integration variables: {", ".join(missing)}')
if os.geteuid() != 0:
    raise SystemExit('This disposable integration gate must run as root for runuser isolation.')

root = Path(__file__).resolve().parents[2]
host = os.environ['OFFICIUM_MYSQL_HOST']
port = int(os.environ['OFFICIUM_MYSQL_PORT'])
user = os.environ['OFFICIUM_MYSQL_USER']
password = os.environ['OFFICIUM_MYSQL_PASSWORD']
database = os.environ['OFFICIUM_MYSQL_DATABASE']
restore_database = os.environ['OFFICIUM_MYSQL_RESTORE_DATABASE']

os.environ.update({
    'OFFICIUM_ENV': 'staging',
    'SECRET_KEY': 'mysql-integration-only-secret-over-32-characters',
    'DB_ENGINE': 'mysql',
    'DB_HOST': host,
    'DB_PORT': str(port),
    'DB_USER': user,
    'DB_PASSWORD': password,
    'DB_NAME': database,
    'SESSION_COOKIE_SECURE': 'true',
    'APP_TIMEZONE': 'Europe/Moscow',
})

from app import create_app  # noqa: E402
from app.models import Expense, Income, User  # noqa: E402
from extensions import db  # noqa: E402

app = create_app({'TESTING': True})
with app.app_context():
    user_row = User(telegram_id=-9301, username='mysql_backup_fixture', role='user')
    db.session.add(user_row)
    db.session.flush()
    db.session.add(Income(
        user_id=user_row.id, amount=Decimal('12345.67'), category='salary',
        source='synthetic staging', income_date=date(2026, 10, 31),
    ))
    db.session.add(Expense(
        user_id=user_row.id, amount=Decimal('2345.67'), category='products',
        title='synthetic staging', expense_date=date(2026, 10, 31),
    ))
    db.session.commit()

connection_args = dict(host=host, port=port, user=user, password=password, charset='utf8mb4')
with pymysql.connect(database=database, **connection_args) as connection:
    with connection.cursor() as cursor:
        cursor.execute('SHOW GRANTS FOR CURRENT_USER')
        grants = '\n'.join(row[0] for row in cursor.fetchall()).upper()
        if 'PROCESS' in grants:
            raise SystemExit('Integration DB user unexpectedly has PROCESS privilege')
        cursor.execute('SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM incomes')
        source_income = cursor.fetchone()
        cursor.execute('SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM expenses')
        source_expense = cursor.fetchone()

runner = runpy.run_path(str(root / 'deployment' / 'officium-update-runner'))
with tempfile.TemporaryDirectory() as app_temp, tempfile.TemporaryDirectory() as backup_temp:
    app_dir = Path(app_temp)
    app_dir.chmod(0o755)
    (app_dir / '.env').write_text(
        '\n'.join((
            'DB_ENGINE=mysql', f'DB_HOST={host}', f'DB_PORT={port}',
            f'DB_USER={user}', f'DB_PASSWORD={password}', f'DB_NAME={database}',
            'APP_TIMEZONE=Europe/Moscow',
        )) + '\n',
        encoding='utf-8',
    )
    dump_path = runner['backup_database']({
        'APP_DIR': str(app_dir),
        'APP_USER': os.environ['OFFICIUM_APP_USER'],
        'BACKUP_DIR': backup_temp,
        'STATUS_FILE': str(app_dir / 'server-update-status.json'),
    }, 'integration')
    if dump_path.stat().st_size < 100:
        raise SystemExit('MySQL dump is unexpectedly small')

    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as options:
        options_path = Path(options.name)
        options.write('[client]\n')
        options.write(f'user="{user}"\npassword="{password}"\nhost="{host}"\nport={port}\n')
    options_path.chmod(0o600)
    try:
        with dump_path.open('rb') as dump:
            restored = subprocess.run(
                ['mysql', f'--defaults-extra-file={options_path}', restore_database],
                stdin=dump,
                timeout=180,
                check=False,
            )
        if restored.returncode != 0:
            raise SystemExit('MySQL restore failed')
    finally:
        options_path.unlink(missing_ok=True)

with pymysql.connect(database=restore_database, **connection_args) as connection:
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM incomes')
        restored_income = cursor.fetchone()
        cursor.execute('SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM expenses')
        restored_expense = cursor.fetchone()
if restored_income != source_income or restored_expense != source_expense:
    raise SystemExit('Restored MySQL key table counts or sums differ')
print('MYSQL_BACKUP_RESTORE_RESULT=PASS no_PROCESS=true counts_and_sums=match')
