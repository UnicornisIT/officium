import mimetypes
import os
import secrets
from datetime import timedelta

import click
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.exc import SQLAlchemyError
from config import Config
from extensions import db
from app.models import AppSetting, ActivityLog, Debt, DictionaryEntry, EmergencyFundTransaction, Expense, FinancialGoal, FinancialGoalTransaction, FinancialPlanPreference, Income, Payment, SplitPurchase, TelegramConversationState, TelegramProcessedUpdate, User
from app.admin_presenters import (
    admin_entity_label,
    admin_event_icon,
    admin_event_label,
    admin_event_tone,
    admin_field_label,
    admin_role_label,
    admin_status_label,
)
from app.services.telegram_frame_service import TelegramFrameSessionInterface, apply_frame_headers
from app.utils import display_value, format_currency

login_manager = LoginManager()
login_manager.login_view = 'login'
_development_secret_key = None


def resolve_instance_path(base_dir, environ=None):
    """Writable instance directory, or None for Flask's <base_dir>/instance.

    A managed release (<APP_ROOT>/releases/<name>, sealed read-only and behind
    ProtectSystem=strict) must never write into itself: its state belongs in
    <APP_ROOT>/shared/instance. OFFICIUM_INSTANCE_PATH wins; otherwise a
    release marked by .officium-release-ready uses the shared directory next
    to it, so installs updated before the variable existed work unchanged.
    """
    environ = os.environ if environ is None else environ
    configured = str(environ.get('OFFICIUM_INSTANCE_PATH', '') or '').strip()
    if configured:
        return os.path.abspath(configured)
    release_dir = os.path.realpath(base_dir)
    if os.path.isfile(os.path.join(release_dir, '.officium-release-ready')):
        shared_instance = os.path.join(os.path.dirname(os.path.dirname(release_dir)), 'shared', 'instance')
        if os.path.isdir(shared_instance):
            return shared_instance
    return None


def create_app(config_overrides=None):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static'),
        static_url_path='/static',
        instance_path=resolve_instance_path(base_dir),
    )
    app.config.from_object(Config)
    if app.config.get('SEND_FILE_MAX_AGE_DEFAULT') is None:
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(days=7)
    if config_overrides:
        app.config.update(config_overrides)

    app.session_interface = TelegramFrameSessionInterface()
    _configure_secret_key(app)
    _validate_runtime_config(app)

    db.init_app(app)
    Migrate(app, db)
    login_manager.init_app(app)
    csrf = CSRFProtect()
    csrf.init_app(app)

    app.jinja_env.filters['money'] = format_currency
    app.jinja_env.filters['display'] = display_value
    app.jinja_env.filters['admin_field'] = admin_field_label
    app.jinja_env.filters['admin_role'] = admin_role_label
    app.jinja_env.filters['admin_status'] = admin_status_label
    app.jinja_env.filters['admin_entity'] = admin_entity_label
    app.jinja_env.filters['admin_event'] = admin_event_label
    app.jinja_env.filters['admin_event_tone'] = admin_event_tone
    app.jinja_env.filters['admin_event_icon'] = admin_event_icon

    from app.routes import auth, admin, debts, documentation, payments, incomes, expenses, goals, main, profile, telegram_bot, whats_new

    auth.init_app(app)
    admin.init_app(app)
    debts.init_app(app)
    documentation.init_app(app)
    payments.init_app(app)
    incomes.init_app(app)
    expenses.init_app(app)
    telegram_bot.init_app(app, csrf=csrf)
    main.init_app(app)
    profile.init_app(app)
    goals.init_app(app)
    whats_new.init_app(app)
    _register_root_icons(app)

    def error_response(status_code):
        messages = {
            400: ('Некорректный запрос', 'Проверьте введённые данные и повторите попытку.'),
            403: ('Доступ запрещён', 'У вас нет прав для выполнения этого действия.'),
            404: ('Страница не найдена', 'Проверьте адрес или вернитесь на главную страницу.'),
            413: (
                'Файл слишком большой',
                'Максимальный размер загружаемого файла — '
                f'{max(1, int(app.config.get("MAX_CONTENT_LENGTH") or 0) // (1024 * 1024))} МБ.',
            ),
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

    @app.errorhandler(413)
    def request_too_large(_error):
        return error_response(413)

    @app.errorhandler(SQLAlchemyError)
    def database_error(_error):
        db.session.rollback()
        app.logger.exception('Database error while handling the request')
        try:
            is_admin_page = request.path.startswith('/admin') and getattr(current_user, 'is_admin', False)
        except SQLAlchemyError:
            db.session.rollback()
            is_admin_page = False
        if is_admin_page:
            return render_template('admin_db_error.html'), 503
        return error_response(500)

    @app.errorhandler(500)
    def internal_error(_error):
        db.session.rollback()
        app.logger.exception('Unhandled application error')
        return error_response(500)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        apply_frame_headers(response, request, app.config)
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
    if (
        app.config.get('TELEGRAM_LOGIN_ENABLED') or app.config.get('TELEGRAM_MINI_APP_ENABLED')
    ) and not str(app.config.get('TELEGRAM_BOT_USERNAME') or '').strip():
        raise RuntimeError(
            'TELEGRAM_BOT_USERNAME is required when Telegram login or Mini App is enabled.'
        )
    if app.config.get('TELEGRAM_BOT_ENABLED') and not app.config.get('TELEGRAM_WEBHOOK_SECRET'):
        raise RuntimeError('TELEGRAM_WEBHOOK_SECRET is required when the Telegram bot is enabled.')
    if app.config.get('GOOGLE_LOGIN_ENABLED') and not (
        app.config.get('GOOGLE_CLIENT_ID') and app.config.get('GOOGLE_CLIENT_SECRET')
    ):
        raise RuntimeError('Google OAuth credentials are required when Google login is enabled.')
    if app.config.get('ADMIN_LOGIN_ENABLED') and not app.config.get('ADMIN_PASSWORD_HASH'):
        raise RuntimeError('An admin password hash is required when emergency admin login is enabled.')


def _register_root_icons(app):
    """Serve the icons browsers and iOS request from the site root without a <link>."""
    mimetypes.add_type('application/manifest+json', '.webmanifest')
    root_icons = {
        '/favicon.ico': 'favicon.ico',
        '/apple-touch-icon.png': 'apple-touch-icon.png',
        '/apple-touch-icon-precomposed.png': 'apple-touch-icon.png',
    }
    for path, filename in root_icons.items():
        endpoint = 'root_icon_' + path.strip('/').replace('.', '_').replace('-', '_')
        app.add_url_rule(
            path,
            endpoint,
            lambda filename=filename: send_from_directory(app.static_folder, filename),
        )


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

    @app.cli.command('import-cleanup')
    def import_cleanup():
        """Remove expired bank statement import previews (run by the maintenance timer)."""
        from app.services.import_storage_service import cleanup_expired, storage_dir

        stats = cleanup_expired(app)
        click.echo(
            f'Import storage {storage_dir(app)}: removed {stats.removed} files ({stats.removed_bytes} bytes), '
            f'kept {stats.kept} ({stats.kept_bytes} bytes), errors {stats.errors}'
        )
        app.logger.info(
            'Import cleanup finished: removed=%s removed_bytes=%s kept=%s kept_bytes=%s errors=%s',
            stats.removed, stats.removed_bytes, stats.kept, stats.kept_bytes, stats.errors,
        )
        if stats.errors:
            raise click.ClickException('Some expired import files could not be removed')

    @app.cli.command('storage-check')
    def storage_check():
        """Report free space of the instance filesystem; exit 2 when it is critical."""
        from app.services.import_storage_service import disk_status

        path = app.instance_path if os.path.isdir(app.instance_path) else os.path.dirname(app.instance_path)
        status = disk_status(
            path,
            int(app.config.get('DISK_WARN_FREE_BYTES')),
            int(app.config.get('DISK_CRITICAL_FREE_BYTES')),
        )
        mib = 1024 * 1024
        line = (
            f'Disk {status.level}: {status.free // mib} MiB free of {status.total // mib} MiB on {status.path} '
            f'(warning below {int(app.config.get("DISK_WARN_FREE_BYTES")) // mib} MiB, '
            f'critical below {int(app.config.get("DISK_CRITICAL_FREE_BYTES")) // mib} MiB)'
        )
        click.echo(line)
        if status.level == 'critical':
            app.logger.critical(line)
            raise SystemExit(2)
        if status.level == 'warning':
            app.logger.warning(line)

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
