import os
import secrets
from datetime import timedelta

import click
from flask import Flask, jsonify, render_template, request
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from config import Config
from extensions import db
from app.models import AppSetting, ActivityLog, Debt, DictionaryEntry, EmergencyFundTransaction, Expense, FinancialGoal, FinancialGoalTransaction, FinancialPlanPreference, Income, Payment, SplitPurchase, TelegramConversationState, TelegramProcessedUpdate, User
from app.utils import display_value, format_currency

login_manager = LoginManager()
login_manager.login_view = 'login'
_development_secret_key = None


def create_app(config_overrides=None):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static'),
        static_url_path='/static'
    )
    app.config.from_object(Config)
    if app.config.get('SEND_FILE_MAX_AGE_DEFAULT') is None:
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(days=7)
    if config_overrides:
        app.config.update(config_overrides)

    _configure_secret_key(app)
    _validate_runtime_config(app)

    db.init_app(app)
    Migrate(app, db)
    login_manager.init_app(app)
    csrf = CSRFProtect()
    csrf.init_app(app)

    app.jinja_env.filters['money'] = format_currency
    app.jinja_env.filters['display'] = display_value

    from app.routes import auth, admin, debts, documentation, payments, incomes, expenses, main, telegram_bot

    auth.init_app(app)
    admin.init_app(app)
    debts.init_app(app)
    documentation.init_app(app)
    payments.init_app(app)
    incomes.init_app(app)
    expenses.init_app(app)
    telegram_bot.init_app(app, csrf=csrf)
    main.init_app(app)

    def error_response(status_code):
        messages = {
            400: ('Некорректный запрос', 'Проверьте введённые данные и повторите попытку.'),
            403: ('Доступ запрещён', 'У вас нет прав для выполнения этого действия.'),
            404: ('Страница не найдена', 'Проверьте адрес или вернитесь на главную страницу.'),
            500: ('Внутренняя ошибка', 'Не удалось выполнить запрос. Повторите попытку позже.'),
        }
        title, message = messages[status_code]
        if request.path.startswith('/api/') or request.path == '/telegram/webhook':
            return jsonify({'success': False, 'error': message}), status_code
        return render_template(
            'error.html',
            status_code=status_code,
            error_title=title,
            error_message=message,
        ), status_code

    @app.errorhandler(400)
    def bad_request(_error):
        return error_response(400)

    @app.errorhandler(403)
    def forbidden(_error):
        return error_response(403)

    @app.errorhandler(404)
    def not_found(_error):
        return error_response(404)

    @app.errorhandler(500)
    def internal_error(_error):
        db.session.rollback()
        app.logger.exception('Unhandled application error')
        return error_response(500)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        if app.config.get('ENVIRONMENT') == 'production':
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response

    register_cli_commands(app)
    return app


def _configure_secret_key(app):
    global _development_secret_key

    if app.config.get('SECRET_KEY'):
        return
    if app.config.get('ENVIRONMENT') != 'development':
        return

    if _development_secret_key is None:
        _development_secret_key = secrets.token_urlsafe(48)
    app.config['SECRET_KEY'] = _development_secret_key
    app.logger.warning(
        'SECRET_KEY is not configured; using a random process-local key for development.'
    )


def _validate_runtime_config(app):
    if app.testing or app.config.get('ENVIRONMENT') != 'production':
        return

    if app.debug:
        raise RuntimeError('DEBUG must be disabled in production.')
    if app.config.get('DEV_LOGIN_ENABLED'):
        raise RuntimeError('DEV_LOGIN_ENABLED must be disabled in production.')
    if app.config.get('TEST_USER_ENABLED'):
        raise RuntimeError('TEST_USER_ENABLED must be disabled in production.')
    if not app.config.get('SESSION_COOKIE_SECURE'):
        raise RuntimeError('SESSION_COOKIE_SECURE must be enabled in production.')

    secret_key = str(app.config.get('SECRET_KEY') or '')
    weak_secrets = {
        '',
        'change-me',
        'dev-secret-change-me',
        '-'.join(('dev', 'secret', 'key', 'change', 'in', 'production')),
    }
    if secret_key in weak_secrets or len(secret_key) < 32:
        raise RuntimeError('SECRET_KEY must be a unique random value of at least 32 characters in production.')
    telegram_features_enabled = any(
        app.config.get(key)
        for key in ('TELEGRAM_LOGIN_ENABLED', 'TELEGRAM_MINI_APP_ENABLED', 'TELEGRAM_BOT_ENABLED')
    )
    if telegram_features_enabled and not app.config.get('TELEGRAM_BOT_TOKEN'):
        raise RuntimeError('TELEGRAM_BOT_TOKEN is required when Telegram features are enabled.')
    if app.config.get('TELEGRAM_BOT_ENABLED') and not app.config.get('TELEGRAM_WEBHOOK_SECRET'):
        raise RuntimeError('TELEGRAM_WEBHOOK_SECRET is required when the Telegram bot is enabled.')
    if app.config.get('GOOGLE_LOGIN_ENABLED') and not (
        app.config.get('GOOGLE_CLIENT_ID') and app.config.get('GOOGLE_CLIENT_SECRET')
    ):
        raise RuntimeError('Google OAuth credentials are required when Google login is enabled.')
    if app.config.get('ADMIN_LOGIN_ENABLED') and not app.config.get('ADMIN_PASSWORD_HASH'):
        raise RuntimeError('An admin password hash is required when emergency admin login is enabled.')


def register_cli_commands(app):
    from app.migration_preflight import register_migration_preflight_commands

    register_migration_preflight_commands(app, db)

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

    @app.cli.command('generate-monthly-expenses')
    @click.option('--user-id', type=int, default=None, help='Generate for specific user ID')
    @click.option('--month', type=str, default=None, help='Target month in YYYY-MM format')
    def generate_monthly_expenses(user_id, month):
        """Generate monthly recurring expenses for the specified or current month."""
        from app.services.monthly_expenses_service import generate_monthly_expenses as gen_expenses
        
        stats = gen_expenses(user_id=user_id, target_month=month)
        click.echo(f"Generated: {stats['created']} expenses")
        click.echo(f"Skipped: {stats['skipped']} (already exist)")
        click.echo(f"Errors: {stats['errors']}")
        app.logger.info(
            'Monthly expense generation finished: target=%s created=%s skipped=%s errors=%s',
            stats['target_month'],
            stats['created'],
            stats['skipped'],
            stats['errors'],
        )
        if stats['errors'] > 0:
            raise click.ClickException('Monthly expense generation completed with errors')

    @app.cli.command('send-telegram-reminders')
    @click.option('--days', type=int, default=None, help='Notify about debt payments due within this many days')
    @click.option('--dry-run', is_flag=True, help='Count reminders without sending Telegram messages')
    def send_telegram_reminders(days, dry_run):
        """Send Telegram reminders about overdue and upcoming debt payments."""
        if not app.config.get('TELEGRAM_BOT_ENABLED', False):
            click.echo('Telegram bot is disabled. Set TELEGRAM_BOT_ENABLED=true to enable reminders.')
            return
        if not app.config.get('TELEGRAM_BOT_TOKEN'):
            click.echo('TELEGRAM_BOT_TOKEN is not configured.')
            return

        from app.services.telegram_bot_service import send_debt_reminders

        stats = send_debt_reminders(days=days, dry_run=dry_run)
        click.echo(f"Users checked: {stats['users_checked']}")
        click.echo(f"Messages: {stats['messages']}")
        click.echo(f"Errors: {stats['errors']}")
