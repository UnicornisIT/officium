import os

import click
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import Config
from extensions import db
from app.models import AppSetting, ActivityLog, Debt, DictionaryEntry, Expense, Income, Payment, User
from app.utils import format_currency

login_manager = LoginManager()
login_manager.login_view = 'login'


def create_app():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static'),
        static_url_path='/static'
    )
    app.config.from_object(Config)

    db.init_app(app)
    Migrate(app, db)
    login_manager.init_app(app)
    csrf = CSRFProtect()
    csrf.init_app(app)

    if app.debug and app.config.get('SQLALCHEMY_DATABASE_URI', '').startswith('sqlite'):
        with app.app_context():
            db.create_all()
            if app.config.get('DEV_SQLITE_COPY_FROM_MYSQL', False):
                _copy_mysql_to_sqlite(app)

    app.jinja_env.filters['money'] = format_currency

    from app.routes import auth, admin, debts, payments, incomes, expenses, main

    auth.init_app(app)
    admin.init_app(app)
    debts.init_app(app)
    payments.init_app(app)
    incomes.init_app(app)
    expenses.init_app(app)
    main.init_app(app)

    return app


def register_cli_commands(app):
    @app.cli.command('create-superadmin')
    @click.argument('telegram_id')
    def create_superadmin(telegram_id):
        from app.models import User

        try:
            telegram_id_int = int(telegram_id)
        except ValueError:
            click.echo('Telegram ID должен быть числом.')
            return

        user = User.query.filter_by(telegram_id=telegram_id_int).first()
        if not user:
            click.echo(f'Пользователь с Telegram ID={telegram_id_int} не найден.')
            return

        user.role = 'superadmin'
        db.session.commit()
        click.echo(f'Пользователь {telegram_id_int} назначен superadmin.')


app = create_app()
register_cli_commands(app)

def _build_mysql_url(config):
    source_url = config.DEV_SQLITE_COPY_SOURCE_URL
    if source_url:
        return source_url

    if config.DATABASE_URL and not config.DATABASE_URL.startswith('sqlite'):
        return config.DATABASE_URL

    if config.DB_ENGINE == 'mysql':
        user = config.DB_USER
        password = config.DB_PASSWORD
        host = config.DB_HOST
        port = config.DB_PORT
        name = config.DB_NAME
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"

    return None


def _copy_mysql_to_sqlite(app):
    mysql_url = _build_mysql_url(app.config)
    if not mysql_url:
        return

    sqlite_session = db.session
    try:
        mysql_engine = create_engine(mysql_url, pool_pre_ping=True)
        mysql_session_factory = sessionmaker(bind=mysql_engine)
        with mysql_session_factory() as mysql_session:
            if User.query.first() is not None:
                return

            for model in (User, AppSetting, DictionaryEntry, Debt, Income, Expense, Payment, ActivityLog):
                source_rows = mysql_session.query(model).all()
                for row in source_rows:
                    row_data = {col.name: getattr(row, col.name) for col in model.__table__.columns}
                    sqlite_session.merge(model(**row_data))
                sqlite_session.commit()
    except Exception:
        sqlite_session.rollback()