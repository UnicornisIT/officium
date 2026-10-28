"""Disposable local web server for Playwright; never use with production data."""

import os
import sys
import tempfile
from pathlib import Path


project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
database_path = Path(tempfile.gettempdir()) / 'officium-playwright.sqlite'
database_path.unlink(missing_ok=True)
os.environ.update({
    'OFFICIUM_ENV': 'development',
    'SECRET_KEY': 'officium-playwright-only-secret-over-32-characters',
    'DB_ENGINE': 'sqlite',
    'SQLITE_PATH': str(database_path),
    'FLASK_DEBUG': 'true',
    'DEV_LOGIN_ENABLED': 'true',
    'TEST_USER_ENABLED': 'false',
    'SESSION_COOKIE_SECURE': 'false',
    'SERVER_UPDATE_ENABLED': 'false',
    'APP_TIMEZONE': 'Europe/Moscow',
    'VEHICLES_ENABLED': 'true',
    # Placeholder bot settings so /telegram-app can flag a dev-login session
    # as Mini App; Telegram itself is never contacted.
    'TELEGRAM_BOT_TOKEN': '123456:playwright-placeholder-token',
    'TELEGRAM_BOT_USERNAME': 'officium_playwright_bot',
    'TELEGRAM_LOGIN_ENABLED': 'true',
    'TELEGRAM_MINI_APP_ENABLED': 'true',
    # Stand-in for https://web.telegram.org in telegram-web-frame.spec.js:
    # browsers block a public HTTPS page from framing 127.0.0.1.
    'TELEGRAM_WEB_FRAME_ANCESTORS': 'https://web.telegram.org http://telegram-web.localhost:5011',
})

from waitress import serve  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402


app = create_app()
with app.app_context():
    db.create_all()
    from app.models import RegistrationCountry
    for index, (code, name) in enumerate([('RU', 'Россия'), ('AB', 'Абхазия'), ('BY', 'Беларусь'), ('KZ', 'Казахстан'), ('AM', 'Армения'), ('KG', 'Кыргызстан'), ('OTHER', 'Другая')]):
        db.session.add(RegistrationCountry(code=code, name=name, strict_format=code == 'RU', sort_order=index,
            flag_path=f'images/flags/{code.lower()}.svg' if code != 'OTHER' else None))
    db.session.commit()

serve(app, listen=os.getenv('E2E_LISTEN', '127.0.0.1:5010'), threads=4)
