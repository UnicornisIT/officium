"""Full bank statement import inside the service sandbox (CI only).

assert-deployment.sh runs this with the release's venv python as the
application user under the same ProtectSystem=strict / ReadWritePaths rules
as officium.service:

    import_probe.py EXPECTED_INSTANCE_PATH

It checks that the release directory is read-only, that the instance path
is the shared one, and that preview -> confirm -> repeated confirm works
against the real database without duplicates. The synthetic QA user and its
expenses are removed afterwards.
"""

import io
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())

from run import app  # noqa: E402
from app.models import Expense, User  # noqa: E402
from extensions import db  # noqa: E402

expected_instance = Path(sys.argv[1])
assert Path(app.instance_path) == expected_instance, (app.instance_path, str(expected_instance))

release = Path(os.getcwd()).resolve()
try:
    (release / '.write-probe').write_text('x', encoding='utf-8')
except OSError as exc:
    print(f'release {release} is read-only for the service ({exc.strerror})')
else:
    (release / '.write-probe').unlink()
    raise SystemExit(f'release {release} is writable by the service')

app.config.update(WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
telegram_id = -(9 * 10**11 + secrets.randbelow(10**9))
with app.app_context():
    user = User(telegram_id=telegram_id, username='qa-import-probe', first_name='QA', role='user')
    db.session.add(user)
    db.session.commit()
    user_id = user.id

try:
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
    statement = (
        'Сбербанк Онлайн\n'
        'Дата операции;Описание операции;Сумма операции;Категория;Карта\n'
        '01.07.2026;QA SYNTHETIC SHOP;-123,45;Супермаркеты;*0000\n'
    ).encode('cp1251')
    preview = client.post(
        '/expenses/import',
        data={'statement_file': (io.BytesIO(statement), 'qa.csv')},
        content_type='multipart/form-data',
    )
    assert preview.status_code == 200, (preview.status_code, preview.get_data(as_text=True)[:400])
    token = re.search(r'name="import_token" value="([^"]+)"', preview.get_data(as_text=True)).group(1)
    stored = expected_instance / 'bank_imports' / f'u{user_id}-{token}.json'
    assert stored.is_file(), stored
    print(f'preview stored as {stored} mode {oct(stored.stat().st_mode & 0o777)}')

    form = {
        'import_token': token, 'row_count': '1', 'action_0': 'create',
        'expense_date_0': '2026-07-01', 'title_0': 'QA SYNTHETIC SHOP', 'category_0': 'products',
        'amount_0': '123.45', 'payment_method_0': 'card', 'comment_0': 'QA import probe',
    }
    first = client.post('/expenses/import/confirm', data=form)
    assert first.status_code == 302, first.status_code
    second = client.post('/expenses/import/confirm', data=form)
    assert second.status_code == 410, second.status_code
    with app.app_context():
        count = Expense.query.filter_by(user_id=user_id).count()
    assert count == 1, count
    assert not stored.exists()
    print('IMPORT PROBE PASS: preview, confirm and repeated confirm (no duplicate)')
finally:
    with app.app_context():
        Expense.query.filter_by(user_id=user_id).delete()
        db.session.delete(db.session.get(User, user_id))
        db.session.commit()
