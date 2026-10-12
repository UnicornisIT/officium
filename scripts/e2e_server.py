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
})

from waitress import serve  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402


app = create_app()
with app.app_context():
    db.create_all()

serve(app, listen=os.getenv('E2E_LISTEN', '127.0.0.1:5010'), threads=4)
