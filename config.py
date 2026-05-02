import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', '')
    ADMIN_LOGIN_ENABLED = os.environ.get('ADMIN_LOGIN_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    ADMIN_TELEGRAM_IDS = [item.strip() for item in os.environ.get('ADMIN_TELEGRAM_IDS', '').split(',') if item.strip()]
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
    ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH', '')
    ADMIN_MAX_LOGIN_ATTEMPTS = int(os.environ.get('ADMIN_MAX_LOGIN_ATTEMPTS', '5'))
    ADMIN_LOCKOUT_MINUTES = int(os.environ.get('ADMIN_LOCKOUT_MINUTES', '15'))
    DEV_LOGIN_ENABLED = os.environ.get('DEV_LOGIN_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes', 'on')
    TEST_USER_ENABLED = os.environ.get('TEST_USER_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
    TEST_USER_TELEGRAM_ID = int(os.environ.get('TEST_USER_TELEGRAM_ID', '-999999999999'))
    TEST_USER_USERNAME = os.environ.get('TEST_USER_USERNAME', 'testuser')
    TEST_USER_FIRST_NAME = os.environ.get('TEST_USER_FIRST_NAME', 'Тестовый')
    TEST_USER_LAST_NAME = os.environ.get('TEST_USER_LAST_NAME', 'Пользователь')
    TEST_USER_ROLE = os.environ.get('TEST_USER_ROLE', 'user')
    
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
            f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
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
