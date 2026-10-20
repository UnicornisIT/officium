import hashlib
from datetime import datetime, timedelta, timezone
from flask import g, jsonify, redirect, render_template, request, url_for, flash, session, abort, current_app
from flask_login import UserMixin, current_user, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash
from authlib.integrations.flask_client import OAuth
from extensions import db
from app import login_manager
from app.models import ActivityLog, User
from app.services import impersonation_service as impersonation
from app.services.contact_service import load_contacts
from app.services.personal_access_service import can_use_personal_cabinet
from app.services.telegram_auth_service import (
    telegram_bot_id,
    verify_telegram_login,
    verify_telegram_web_app_init_data,
)
from app.time_utils import utc_now
from app.utils import client_ip, get_dictionary_groups, get_setting, payment_thresholds, record_activity, setting_enabled, telegram_feature_states


class AdminUser(UserMixin):
    def __init__(self):
        self.id = 'admin'
        self.username = 'admin'
        self.first_name = 'Администратор'
        self.role = 'superadmin'
        self.is_blocked = False

    @property
    def is_admin(self):
        return True

    @property
    def is_superadmin(self):
        return True

    def get_id(self):
        return str(self.id)


class LocalTestUser(UserMixin):
    def __init__(self):
        self.id = 'test-user'
        self.username = 'testuser'
        self.first_name = 'Тестовый'
        self.last_name = 'Пользователь'
        self.role = 'user'
        self.is_blocked = False
        self.login_count = 1
        self.last_login_ip = None
        self.last_user_agent = None
        self.is_local_test_user = True

    @property
    def is_admin(self):
        return False

    @property
    def is_superadmin(self):
        return False

    def get_id(self):
        return str(self.id)


EMERGENCY_LOGIN_SUCCESS = 'Аварийный вход администратора'
EMERGENCY_LOGIN_FAILURE = 'Неудачный аварийный вход'


def _emergency_login_failures(ip_address, lockout_minutes):
    """Count failed emergency logins from this address inside the lockout window.

    The counter lives in the activity log, so clearing cookies does not reset it;
    a successful login from the same address starts a new window.
    """
    window_start = (utc_now() - timedelta(minutes=lockout_minutes)).replace(tzinfo=None)
    last_success = db.session.query(func.max(ActivityLog.created_at)).filter(
        ActivityLog.action == EMERGENCY_LOGIN_SUCCESS,
        ActivityLog.ip_address == ip_address,
    ).scalar()
    if last_success and last_success > window_start:
        window_start = last_success
    return ActivityLog.query.filter(
        ActivityLog.action == EMERGENCY_LOGIN_FAILURE,
        ActivityLog.ip_address == ip_address,
        ActivityLog.created_at >= window_start,
    ).count()


def get_or_create_test_user(app):
    test_user_telegram_id = app.config.get('TEST_USER_TELEGRAM_ID', -999999999999)
    test_user = User.query.filter_by(telegram_id=test_user_telegram_id).first()
    if not test_user:
        test_user = User(
            telegram_id=test_user_telegram_id,
            username=app.config.get('TEST_USER_USERNAME', 'testuser'),
            first_name=app.config.get('TEST_USER_FIRST_NAME', 'Тестовый'),
            last_name=app.config.get('TEST_USER_LAST_NAME', 'Пользователь'),
            auth_date=utc_now(),
            role=app.config.get('TEST_USER_ROLE', 'user'),
            is_blocked=False,
            login_count=0,
        )
        db.session.add(test_user)
        db.session.commit()
    elif test_user.is_blocked:
        test_user.is_blocked = False
        db.session.commit()
    return test_user


def init_app(app):
    oauth = OAuth(app)

    google = oauth.register(
        name='google',
        client_id=app.config.get('GOOGLE_CLIENT_ID'),
        client_secret=app.config.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid_configuration',
        client_kwargs={
            'scope': 'openid email profile'
        }
    )

    @login_manager.user_loader
    def load_user(user_id):
        if not user_id:
            return None
        if str(user_id) == 'admin':
            return AdminUser()
        if str(user_id) == 'test-user':
            if not app.config.get('TEST_USER_ENABLED', False):
                return None
            try:
                return get_or_create_test_user(app)
            except SQLAlchemyError:
                db.session.rollback()
                return LocalTestUser()
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Требуется авторизация'}), 401
        return redirect(url_for('login'))

    @app.before_request
    def require_login():
        allowed_endpoints = {
            'static',
            'root_icon_favicon_ico',
            'root_icon_apple_touch_icon_png',
            'root_icon_apple_touch_icon_precomposed_png',
            'login',
            'telegram_login',
            'telegram_mini_app',
            'telegram_mini_app_login',
            'telegram_webhook',
            'google_login',
            'google_callback',
            'logout',
            'admin_login',
            'test_login',
            'documentation',
            'dev_login',
            'dev_logout',
            'stop_impersonate',
        }
        if session.get('original_admin_id') and request.endpoint not in ('static', 'stop_impersonate', 'logout'):
            response = _enforce_impersonation()
            if response is not None:
                return response

        if request.endpoint in allowed_endpoints:
            return

        # The URL selects the mode: /admin is the panel, everything else is the personal cabinet.
        if (
            getattr(current_user, 'is_authenticated', False)
            and not request.path.startswith('/admin')
            and not can_use_personal_cabinet(current_user)
        ):
            return redirect(url_for('admin_dashboard'))

        if getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'is_blocked', False):
            if session.get('original_admin_id'):
                admin = impersonation.finish('blocked', restore=True, emergency_enabled=_emergency_enabled())
                flash('Пользователь заблокирован, сеанс входа от его имени завершён.', 'warning')
                return redirect(url_for('admin_dashboard' if admin else 'login'))
            logout_user()
            if request.path.startswith('/api/'):
                return {'success': False, 'error': 'Учетная запись заблокирована'}, 403
            return render_template('login.html', error='Ваша учётная запись заблокирована.', bot_username=app.config.get('TELEGRAM_BOT_USERNAME', ''), telegram_allowed=False)

        if not getattr(current_user, 'is_authenticated', False):
            return unauthorized()

    def _emergency_enabled():
        return bool(app.config.get('ADMIN_LOGIN_ENABLED', False))

    def _impersonation_reply(message, target_endpoint):
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': message, 'code': 'impersonation_ended'}), 401
        flash(message, 'info')
        return redirect(url_for(target_endpoint))

    def _enforce_impersonation():
        """End an impersonation session that is invalid or past its time limit."""
        record = impersonation.current_record()
        valid = (
            record is not None
            and record.ended_at is None
            and getattr(current_user, 'is_authenticated', False)
            and session.get('impersonated_user_id') == str(current_user.get_id())
        )
        if not valid:
            impersonation.finish('invalid', restore=False, emergency_enabled=_emergency_enabled())
            return _impersonation_reply('Сеанс входа от имени пользователя недействителен. Войдите снова.', 'login')
        if getattr(current_user, 'is_admin', False):
            # The target became an administrator: their cabinet must not mix with the panel.
            admin = impersonation.finish('role_changed', restore=True, emergency_enabled=_emergency_enabled())
            return _impersonation_reply(
                'Пользователь получил права администратора, сеанс входа от его имени завершён.',
                'admin_dashboard' if admin else 'login',
            )
        if impersonation.is_expired(record):
            admin = impersonation.finish('timeout', restore=True, emergency_enabled=_emergency_enabled())
            return _impersonation_reply(
                'Время сеанса от имени пользователя истекло, вы вернулись в административную панель.',
                'admin_dashboard' if admin else 'login',
            )
        # Marks requests made inside an already running session (see log_changes_on_behalf).
        g.acting_impersonation_id = record.id
        if record.read_only and request.method not in impersonation.SAFE_METHODS:
            message = 'Режим «только просмотр»: изменения от имени пользователя запрещены.'
            if request.path.startswith('/api/') or request.is_json:
                return jsonify({'success': False, 'error': message, 'code': 'impersonation_read_only'}), 403
            flash(message, 'warning')
            return redirect(request.referrer or url_for('index'), code=303)
        return None

    @app.after_request
    def log_changes_on_behalf(response):
        if (
            request.method in impersonation.SAFE_METHODS
            or request.endpoint in ('stop_impersonate', 'logout', 'static')
            or not g.get('acting_impersonation_id')
            or response.status_code >= 400
        ):
            return response
        record = impersonation.current_record()
        if record is not None and record.ended_at is None and not record.read_only:
            try:
                impersonation.record_change(record)
            except SQLAlchemyError:
                db.session.rollback()
                app.logger.exception('Failed to record a change made on behalf of a user')
        return response

    @app.context_processor
    def inject_user():
        app_name = get_setting('app_name', 'officium')
        dictionary_groups = get_dictionary_groups()
        admin_login_enabled = app.config.get('ADMIN_LOGIN_ENABLED', False)
        test_login_enabled = app.config.get('TEST_USER_ENABLED', False)
        dev_login_enabled = app.config.get('DEV_LOGIN_ENABLED', False) and app.debug
        is_impersonating = session.get('original_admin_id') is not None
        impersonation_banner = None
        if is_impersonating and getattr(current_user, 'is_authenticated', False):
            record = impersonation.current_record()
            if record is not None and record.ended_at is None:
                impersonation_banner = {
                    'name': record.target_label,
                    'display_name': (
                        ' '.join(filter(None, [getattr(current_user, 'first_name', None), getattr(current_user, 'last_name', None)]))
                        or getattr(current_user, 'username', None)
                        or record.target_label
                    ),
                    'minutes_left': impersonation.minutes_left(record),
                    'read_only': record.read_only,
                    'expires_at': record.expires_at.isoformat() + 'Z',
                }
        is_telegram_mini_app = bool(session.get('telegram_mini_app'))
        mini_app_short_name = app.config.get('TELEGRAM_MINI_APP_SHORT_NAME', '')
        telegram_states = telegram_feature_states(app.config)
        mini_app_enabled = telegram_states['mini_app']['effective']
        telegram_login_launch = None
        if not getattr(current_user, 'is_authenticated', False) and not is_telegram_mini_app:
            # One launcher config for the /login button and the header "Войти" link.
            launch_bot_id = telegram_bot_id(app.config.get('TELEGRAM_BOT_TOKEN', ''))
            telegram_login_launch = {
                'available': bool(telegram_states['login']['effective'] and launch_bot_id),
                'bot_id': launch_bot_id if telegram_states['login']['effective'] else None,
                'auth_url': url_for('telegram_login'),
            }
        return dict(
            current_user=current_user,
            app_name=app_name,
            bank_options=dictionary_groups.get('bank', []),
            product_options=dictionary_groups.get('product_type', []),
            interest_rate_options=dictionary_groups.get('interest_rate', []),
            comment_templates=dictionary_groups.get('comment_template', []),
            admin_login_enabled=admin_login_enabled,
            test_login_enabled=test_login_enabled,
            dev_login_enabled=dev_login_enabled,
            is_impersonating=is_impersonating,
            can_use_personal_cabinet=can_use_personal_cabinet(current_user),
            impersonation_banner=impersonation_banner,
            archive_enabled=setting_enabled('archive_enabled', True),
            payment_days=payment_thresholds(),
            is_telegram_mini_app=is_telegram_mini_app,
            telegram_mini_app_enabled=mini_app_enabled,
            telegram_login_launch=telegram_login_launch,
            telegram_mini_app_short_name=mini_app_short_name,
            public_contacts_footer=load_contacts(published_only=True, location='footer'),
            public_contacts_login=load_contacts(published_only=True, location='login'),
            public_contacts_help=load_contacts(published_only=True, location='help'),
            public_contacts_mini_app=load_contacts(published_only=True, location='mini_app'),
        )

    def _telegram_profile_value(profile, key, max_length):
        value = profile.get(key)
        if value is None:
            return None
        return str(value).strip()[:max_length] or None

    def _setting_enabled(key, default=False):
        value = get_setting(key, 'true' if default else 'false')
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on')

    def _google_telegram_id(google_id):
        digest = hashlib.sha256(str(google_id).encode('utf-8')).digest()
        candidate = -(int.from_bytes(digest[:8], 'big') % 9_000_000_000_000_000_000 or 1)
        while User.query.filter_by(telegram_id=candidate).first() is not None:
            candidate -= 1
        return candidate

    def _login_telegram_profile(profile, auth_timestamp, activity_name):
        try:
            telegram_id = int(profile.get('id'))
            auth_date = datetime.fromtimestamp(int(auth_timestamp), timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None, 'Неверные данные авторизации Telegram.'

        if telegram_id <= 0:
            return None, 'Неверные данные авторизации Telegram.'

        admin_ids = [item.strip() for item in app.config.get('ADMIN_TELEGRAM_IDS', []) if item.strip()]
        is_admin_user = str(telegram_id) in admin_ids
        user = User.query.filter_by(telegram_id=telegram_id).first()
        if not user:
            if not is_admin_user and not _setting_enabled('registration_enabled', False):
                return None, 'Регистрация новых пользователей временно отключена.'
            user = User(
                telegram_id=telegram_id,
                auth_date=auth_date,
                role='superadmin' if is_admin_user else 'user',
            )
            db.session.add(user)
        elif user.is_blocked:
            return None, 'Ваш аккаунт заблокирован. Обратитесь к администратору.'

        if is_admin_user and not user.is_superadmin:
            user.role = 'superadmin'
        user.username = _telegram_profile_value(profile, 'username', 80)
        user.first_name = _telegram_profile_value(profile, 'first_name', 100)
        user.last_name = _telegram_profile_value(profile, 'last_name', 100)
        user.photo_url = _telegram_profile_value(profile, 'photo_url', 255)
        user.auth_date = auth_date
        user.login_count = (user.login_count or 0) + 1
        user.last_login_ip = client_ip()
        user.last_user_agent = request.headers.get('User-Agent')

        db.session.commit()
        record_activity(
            activity_name,
            user,
            description=f'Telegram ID={telegram_id}',
            ip_address=client_ip(),
            user_agent=request.headers.get('User-Agent'),
        )
        login_user(user)
        return user, None

    def _landing_url():
        # Web sign-in opens the admin panel for administrators, as before personal access existed.
        return url_for('admin_dashboard' if getattr(current_user, 'is_admin', False) else 'index')

    @app.route('/login')
    def login():
        if current_user.is_authenticated:
            return redirect(_landing_url())
        telegram_allowed = telegram_feature_states(app.config)['login']['effective']
        return render_template(
            'login.html',
            bot_username=app.config.get('TELEGRAM_BOT_USERNAME', ''),
            telegram_allowed=telegram_allowed,
            admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False),
            test_login_enabled=app.config.get('TEST_USER_ENABLED', False),
            google_login_enabled=app.config.get('GOOGLE_LOGIN_ENABLED', False),
        )

    @app.route('/telegram-app')
    def telegram_mini_app():
        if not telegram_feature_states(app.config)['mini_app']['effective']:
            abort(404)
        if current_user.is_authenticated:
            session['telegram_mini_app'] = True
            return redirect(url_for('index'))
        return render_template(
            'telegram_app.html',
            bot_username=app.config.get('TELEGRAM_BOT_USERNAME', ''),
            mini_app_short_name=app.config.get('TELEGRAM_MINI_APP_SHORT_NAME', ''),
        )

    @app.route('/auth/telegram-mini-app', methods=['POST'])
    def telegram_mini_app_login():
        if not telegram_feature_states(app.config)['mini_app']['effective']:
            return jsonify({'success': False, 'error': 'Telegram Mini App отключён.'}), 404

        telegram_allowed = telegram_feature_states(app.config)['login']['effective']
        if not telegram_allowed:
            return jsonify({'success': False, 'error': 'Вход через Telegram временно отключён.'}), 403

        payload = request.get_json(silent=True) or {}
        init_data = payload.get('init_data', '')
        parsed = verify_telegram_web_app_init_data(
            init_data,
            app.config.get('TELEGRAM_BOT_TOKEN', ''),
            max_age_seconds=app.config.get('TELEGRAM_WEB_APP_AUTH_MAX_AGE_SECONDS', 86400),
        )
        if not parsed:
            return jsonify({'success': False, 'error': 'Не удалось подтвердить запуск через Telegram.'}), 401

        user, error = _login_telegram_profile(
            parsed['user'],
            parsed['auth_date'],
            'Вход через Telegram Mini App',
        )
        if error:
            status = 403 if user is None and (
                'заблокирован' in error or 'Регистрация' in error
            ) else 400
            return jsonify({'success': False, 'error': error}), status

        session['telegram_mini_app'] = True
        return jsonify({'success': True, 'next_url': url_for('index')})

    def _dev_mode_enabled():
        return app.debug and app.config.get('DEV_LOGIN_ENABLED', False)

    DEV_LOGIN_USERS = {
        'user': {
            'telegram_id': -1000000001,
            'username': 'dev_user',
            'first_name': 'Dev',
            'last_name': 'User',
            'role': 'user',
        },
        'admin': {
            'telegram_id': -1000000002,
            'username': 'dev_admin',
            'first_name': 'Dev',
            'last_name': 'Admin',
            'role': 'admin',
        },
        'superadmin': {
            'telegram_id': -1000000003,
            'username': 'dev_superadmin',
            'first_name': 'Dev',
            'last_name': 'Superadmin',
            'role': 'superadmin',
        },
    }

    @app.route('/dev-login/<string:role>')
    def dev_login(role):
        if not _dev_mode_enabled() or role not in DEV_LOGIN_USERS:
            abort(404)

        logout_user()

        dev_user_data = DEV_LOGIN_USERS[role]
        user = User.query.filter_by(telegram_id=dev_user_data['telegram_id']).first()
        if not user:
            user = User(
                telegram_id=dev_user_data['telegram_id'],
                username=dev_user_data['username'],
                first_name=dev_user_data['first_name'],
                last_name=dev_user_data['last_name'],
                auth_date=utc_now(),
                role=dev_user_data['role'],
                is_blocked=False,
            )
            db.session.add(user)

        user.role = dev_user_data['role']
        user.username = dev_user_data['username']
        user.first_name = dev_user_data['first_name']
        user.last_name = dev_user_data['last_name']
        user.is_blocked = False
        user.login_count = (user.login_count or 0) + 1
        user.last_login_ip = client_ip()
        user.last_user_agent = request.headers.get('User-Agent')
        db.session.commit()

        record_activity(
            'Dev login',
            user,
            description=f'Вход в режиме разработки как {role}',
            ip_address=client_ip(),
            user_agent=request.headers.get('User-Agent'),
        )

        login_user(user)
        flash(f'Выполнен вход в режиме разработки как {role}.', 'success')
        if role == 'user':
            return redirect(url_for('index'))
        return redirect(url_for('admin_dashboard'))

    @app.route('/dev-logout')
    def dev_logout():
        if not _dev_mode_enabled():
            abort(404)
        logout_user()
        flash('Выход из режима разработки выполнен.', 'success')
        return redirect(url_for('login'))

    @app.route('/test-login')
    def test_login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))

        test_user_enabled = app.config.get('TEST_USER_ENABLED', False)
        if not test_user_enabled:
            return render_template(
                'login.html',
                error='Тестовый доступ отключён.',
                bot_username=app.config.get('TELEGRAM_BOT_USERNAME', ''),
                telegram_allowed=False,
                admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False),
                test_login_enabled=False,
            )

        try:
            test_user = get_or_create_test_user(app)
            test_user.login_count = (test_user.login_count or 0) + 1
            test_user.last_login_ip = client_ip()
            test_user.last_user_agent = request.headers.get('User-Agent')
            db.session.commit()
            login_user(test_user)
            return redirect(url_for('index'))
        except SQLAlchemyError:
            login_user(LocalTestUser())
            return redirect(url_for('index', login_mode='local-test'))

    @app.route('/telegram-login')
    def telegram_login():
        data = request.args.to_dict()
        bot_username = app.config.get('TELEGRAM_BOT_USERNAME', '')

        telegram_allowed = telegram_feature_states(app.config)['login']['effective']
        if not telegram_allowed:
            return render_template('login.html', error='Вход через Telegram временно отключён администратором.', bot_username=bot_username, telegram_allowed=False, admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False))

        if not verify_telegram_login(data, app.config.get('TELEGRAM_BOT_TOKEN', '')):
            return render_template('login.html', error='Ошибка авторизации через Telegram. Проверьте настройки бота.', bot_username=bot_username, telegram_allowed=telegram_allowed, admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False))

        if not data.get('id'):
            return render_template('login.html', error='Неверные данные авторизации.', bot_username=bot_username, telegram_allowed=telegram_allowed, admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False))

        profile = {
            'id': data.get('id'),
            'username': data.get('username'),
            'first_name': data.get('first_name'),
            'last_name': data.get('last_name'),
            'photo_url': data.get('photo_url'),
        }
        _, error = _login_telegram_profile(profile, data.get('auth_date'), 'Вход через Telegram')
        if error:
            return render_template(
                'login.html',
                error=error,
                bot_username=bot_username,
                telegram_allowed='заблокирован' not in error,
                admin_login_enabled=app.config.get('ADMIN_LOGIN_ENABLED', False),
            )
        session.pop('telegram_mini_app', None)
        return redirect(_landing_url())

    @app.route('/auth/google')
    def google_login():
        if not app.config.get('GOOGLE_LOGIN_ENABLED', False):
            abort(404)
        redirect_uri = app.config.get('GOOGLE_REDIRECT_URI') or url_for('google_callback', _external=True)
        return google.authorize_redirect(redirect_uri)

    @app.route('/auth/google/callback')
    def google_callback():
        if not app.config.get('GOOGLE_LOGIN_ENABLED', False):
            abort(404)
        try:
            token = google.authorize_access_token()
            userinfo = google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        except Exception:
            current_app.logger.exception('Google OAuth callback failed')
            flash('Ошибка авторизации через Google.', 'danger')
            return redirect(url_for('login'))

        google_id = str(userinfo.get('sub') or '').strip()[:50]
        email = str(userinfo.get('email') or '').strip().lower()[:120]
        email_verified = userinfo.get('email_verified', False)
        name = str(userinfo.get('name') or email.split('@')[0])
        picture = userinfo.get('picture')

        if not google_id or not email:
            flash('Недостаточно данных от Google для авторизации.', 'danger')
            return redirect(url_for('login'))

        if not email_verified:
            flash('Email не подтверждён в Google.', 'danger')
            return redirect(url_for('login'))

        user = User.query.filter_by(google_id=google_id).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if user:
                # Привязываем Google ID к существующему пользователю
                user.google_id = google_id
                user.avatar_url = str(picture or '')[:255] or None
            else:
                if not _setting_enabled('registration_enabled', False):
                    flash('Регистрация новых пользователей временно отключена.', 'warning')
                    return redirect(url_for('login'))
                # Создаём нового пользователя
                first_name, last_name = (name.split(' ', 1) + [''])[:2]
                user = User(
                    telegram_id=_google_telegram_id(google_id),
                    username=email.split('@')[0][:80],
                    first_name=first_name[:100],
                    last_name=last_name[:100],
                    photo_url=str(picture or '')[:255] or None,
                    auth_date=utc_now(),
                    role='user',
                    google_id=google_id,
                    email=email,
                    avatar_url=str(picture or '')[:255] or None,
                )
                db.session.add(user)

        if user.is_blocked:
            flash('Ваша учётная запись заблокирована.', 'danger')
            return redirect(url_for('login'))

        user.login_count = (user.login_count or 0) + 1
        user.last_login_ip = client_ip()
        user.last_user_agent = request.headers.get('User-Agent')

        db.session.commit()
        record_activity('Вход через Google', user, description=f'Google ID={google_id}, email={email}', ip_address=client_ip(), user_agent=request.headers.get('User-Agent'))
        login_user(user)
        return redirect(_landing_url())

    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
            return redirect(url_for('admin_dashboard'))

        admin_login_enabled = app.config.get('ADMIN_LOGIN_ENABLED', False)
        admin_password_hash = app.config.get('ADMIN_PASSWORD_HASH', '')
        max_attempts = app.config.get('ADMIN_MAX_LOGIN_ATTEMPTS', 5)
        lockout_minutes = app.config.get('ADMIN_LOCKOUT_MINUTES', 15)
        error_message = None

        locked_message = 'Слишком много неудачных попыток. Попробуйте через некоторое время.'
        ip_address = client_ip()
        user_agent = request.headers.get('User-Agent')

        if request.method == 'POST':
            try:
                failures = _emergency_login_failures(ip_address, lockout_minutes)
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception('Failed to read emergency login attempts')
                failures = None
                error_message = 'Вход временно недоступен: база данных не отвечает.'

            if failures is not None and failures >= max_attempts:
                error_message = locked_message
            elif failures is not None and (not admin_login_enabled or not admin_password_hash):
                error_message = 'Админ-вход отключён или пароль не настроен.'
            elif failures is not None:
                password = request.form.get('password', '')
                if check_password_hash(admin_password_hash, password):
                    login_user(AdminUser())
                    record_activity(EMERGENCY_LOGIN_SUCCESS, None, entity_type='auth', description='Успешный вход', ip_address=ip_address, user_agent=user_agent)
                    return redirect(url_for('admin_dashboard'))

                attempt = failures + 1
                record_activity(EMERGENCY_LOGIN_FAILURE, None, entity_type='auth', description=f'Неверный пароль ({attempt}/{max_attempts})', ip_address=ip_address, user_agent=user_agent)
                error_message = locked_message if attempt >= max_attempts else 'Неверный пароль администратора.'

        return render_template('admin_login.html', admin_login_enabled=admin_login_enabled, error_message=error_message)

    @app.route('/admin/stop-impersonate', methods=['POST'])
    def stop_impersonate():
        # Restore the admin only from the very session that started impersonation.
        if (
            not session.get('original_admin_id')
            or not getattr(current_user, 'is_authenticated', False)
            or session.get('impersonated_user_id') != str(current_user.get_id())
        ):
            impersonation.clear_session()
            return redirect(url_for('login'))

        admin = impersonation.finish('manual', restore=True, emergency_enabled=_emergency_enabled())
        return redirect(url_for('admin_dashboard' if admin else 'login'))

    @app.route('/logout')
    def logout():
        if session.get('original_admin_id'):
            impersonation.finish('logout', restore=False, emergency_enabled=_emergency_enabled())
        logout_user()
        session.pop('telegram_mini_app', None)
        impersonation.clear_session()
        return redirect(url_for('login'))
