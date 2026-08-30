import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()

class Config:
    ENVIRONMENT = os.environ.get('OFFICIUM_ENV', 'development').strip().lower()
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', '')
    TELEGRAM_LOGIN_ENABLED = os.environ.get('TELEGRAM_LOGIN_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on')
    TELEGRAM_MINI_APP_ENABLED = os.environ.get('TELEGRAM_MINI_APP_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on')
    TELEGRAM_MINI_APP_SHORT_NAME = os.environ.get('TELEGRAM_MINI_APP_SHORT_NAME', '')
    TELEGRAM_WEB_APP_AUTH_MAX_AGE_SECONDS = int(os.environ.get('TELEGRAM_WEB_APP_AUTH_MAX_AGE_SECONDS', '86400'))
    TELEGRAM_BOT_ENABLED = os.environ.get('TELEGRAM_BOT_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    TELEGRAM_WEBHOOK_SECRET = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')
    TELEGRAM_PRIVATE_CHAT_ONLY = os.environ.get('TELEGRAM_PRIVATE_CHAT_ONLY', 'true').lower() in ('1', 'true', 'yes', 'on')
    TELEGRAM_BOT_RATE_LIMIT_PER_MINUTE = int(os.environ.get('TELEGRAM_BOT_RATE_LIMIT_PER_MINUTE', '20'))
    TELEGRAM_REMINDER_DAYS = int(os.environ.get('TELEGRAM_REMINDER_DAYS', '7'))
    TELEGRAM_UPDATE_RETENTION_DAYS = int(os.environ.get('TELEGRAM_UPDATE_RETENTION_DAYS', '30'))
    TELEGRAM_CONVERSATION_TTL_MINUTES = int(os.environ.get('TELEGRAM_CONVERSATION_TTL_MINUTES', '30'))
    ADMIN_LOGIN_ENABLED = os.environ.get('ADMIN_LOGIN_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    ADMIN_TELEGRAM_IDS = [item.strip() for item in os.environ.get('ADMIN_TELEGRAM_IDS', '').split(',') if item.strip()]
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
    ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH', '')
    ADMIN_MAX_LOGIN_ATTEMPTS = int(os.environ.get('ADMIN_MAX_LOGIN_ATTEMPTS', '5'))
    ADMIN_LOCKOUT_MINUTES = int(os.environ.get('ADMIN_LOCKOUT_MINUTES', '15'))
    APP_VERSION = os.environ.get('APP_VERSION', '')
    SERVER_UPDATE_ENABLED = os.environ.get('SERVER_UPDATE_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    SERVER_UPDATE_REPOSITORY = os.environ.get('SERVER_UPDATE_REPOSITORY', 'UnicornisIT/officium')
    SERVER_UPDATE_GITHUB_TOKEN = os.environ.get('SERVER_UPDATE_GITHUB_TOKEN', '')
    SERVER_UPDATE_HELPER = os.environ.get('SERVER_UPDATE_HELPER', '')
    SERVER_UPDATE_USE_SUDO = os.environ.get('SERVER_UPDATE_USE_SUDO', 'true').lower() in ('1', 'true', 'yes', 'on')
    SERVER_UPDATE_SUDO_PATH = os.environ.get('SERVER_UPDATE_SUDO_PATH', '/usr/bin/sudo')
    SERVER_UPDATE_APP_DIR = os.environ.get('SERVER_UPDATE_APP_DIR', basedir)
    SERVER_UPDATE_STATUS_PATH = os.environ.get(
        'SERVER_UPDATE_STATUS_PATH',
        os.path.join(basedir, 'instance', 'server-update-status.json'),
    )
    SERVER_UPDATE_HTTP_TIMEOUT_SECONDS = int(os.environ.get('SERVER_UPDATE_HTTP_TIMEOUT_SECONDS', '8'))
    SERVER_UPDATE_COMMAND_TIMEOUT_SECONDS = int(os.environ.get('SERVER_UPDATE_COMMAND_TIMEOUT_SECONDS', '15'))
    SERVER_UPDATE_STALE_MINUTES = int(os.environ.get('SERVER_UPDATE_STALE_MINUTES', '120'))
    SERVER_UPDATE_REQUIRE_ROOT_OWNED_HELPER = os.environ.get(
        'SERVER_UPDATE_REQUIRE_ROOT_OWNED_HELPER', 'true'
    ).lower() in ('1', 'true', 'yes', 'on')
    DEV_LOGIN_ENABLED = os.environ.get('DEV_LOGIN_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes', 'on')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get(
        'SESSION_COOKIE_SECURE',
        'true' if ENVIRONMENT == 'production' else 'false',
    ).lower() in ('1', 'true', 'yes', 'on')
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', str(10 * 1024 * 1024)))
    TEST_USER_ENABLED = os.environ.get('TEST_USER_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    TEST_USER_TELEGRAM_ID = int(os.environ.get('TEST_USER_TELEGRAM_ID', '-999999999999'))
    TEST_USER_USERNAME = os.environ.get('TEST_USER_USERNAME', 'testuser')
    TEST_USER_FIRST_NAME = os.environ.get('TEST_USER_FIRST_NAME', 'Тестовый')
    TEST_USER_LAST_NAME = os.environ.get('TEST_USER_LAST_NAME', 'Пользователь')
    TEST_USER_ROLE = os.environ.get('TEST_USER_ROLE', 'user')
    
    GOOGLE_LOGIN_ENABLED = os.environ.get('GOOGLE_LOGIN_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'http://127.0.0.1:5000/auth/google/callback')
    
    DATABASE_URL = os.environ.get('DATABASE_URL', '')
    DB_ENGINE = os.environ.get('DB_ENGINE', 'mysql').lower()
    SQLITE_PATH = os.environ.get('SQLITE_PATH', 'dev.db')
    DEV_SQLITE_COPY_FROM_MYSQL = os.environ.get('DEV_SQLITE_COPY_FROM_MYSQL', 'false').lower() in ('1', 'true', 'yes', 'on')
    DEV_SQLITE_COPY_SOURCE_URL = os.environ.get('DEV_SQLITE_COPY_SOURCE_URL', '')

    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '3306')
    DB_USER = os.environ.get('DB_USER', 'root')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    DB_NAME = os.environ.get('DB_NAME', 'debt_manager')
    
    if DATABASE_URL:
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    elif DB_ENGINE == 'sqlite':
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(basedir, SQLITE_PATH)}"
    else:
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            f"?charset=utf8mb4"
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    if DB_ENGINE == 'sqlite' or DATABASE_URL.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS = {}
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_recycle': 300,
            'pool_pre_ping': True,
        }
