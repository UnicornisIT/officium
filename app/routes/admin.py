import csv
from datetime import datetime, timedelta
from io import StringIO
from flask import abort, current_app, jsonify, redirect, render_template, request, url_for, Response, flash
from flask_login import current_user
from sqlalchemy import and_, cast, or_, text
from sqlalchemy.exc import SQLAlchemyError
from app.models import ActivityLog, DictionaryEntry, User
from app.services import impersonation_service as impersonation
from app.services.contact_service import (
    CONTACT_ICONS,
    CONTACT_LOCATIONS,
    CONTACT_TYPES,
    ContactValidationError,
    delete_contact,
    load_contacts,
    save_contact,
)
from app.services.server_update_service import (
    ServerUpdateError,
    check_update_readiness,
    current_release_has_tag,
    fetch_latest_release,
    get_current_release,
    get_update_status,
    read_update_status,
    reconcile_update_status,
    request_server_update,
    update_is_active,
)
from app.time_utils import utc_now
from app.utils import client_ip, admin_required, superadmin_required, DICTIONARY_HELP, DICTIONARY_TYPES, DEFAULT_SETTINGS, normalize_dictionary_value, get_setting, record_activity, set_setting, telegram_feature_states
from extensions import db


def _count_superadmins():
    return User.query.filter_by(role='superadmin').count()


def _is_last_superadmin(user):
    return user.is_superadmin and _count_superadmins() <= 1


def _setting_enabled(key, default=True):
    value = get_setting(key, 'true' if default else 'false')
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _test_impersonation_enabled():
    # TEST_USER_ENABLED is rejected in production by the runtime config check.
    return bool(current_app.config.get('TEST_USER_ENABLED'))


def _page_number():
    return max(request.args.get('page', 1, type=int) or 1, 1)


def _filter_datetime(value, *, end=False):
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), '%Y-%m-%d')
    except ValueError:
        return None
    return parsed + timedelta(days=1) if end else parsed


def _activity_log_query(args):
    """Build the activity-log query shared by the journal page and its export."""
    filters = {
        'user': args.get('user', type=int),
        'actor': (args.get('actor') or '').strip()[:100],
        'event': (args.get('event') or '').strip(),
        'entity': (args.get('entity') or '').strip(),
        'date_from': (args.get('date_from') or '').strip(),
        'date_to': (args.get('date_to') or '').strip(),
        'impersonation': args.get('impersonation') in ('1', 'on', 'true'),
    }
    query = ActivityLog.query
    if filters['user']:
        query = query.filter(ActivityLog.user_id == filters['user'])
    if filters['actor']:
        pattern = f"%{filters['actor']}%"
        matching_users = db.session.query(User.id).filter(or_(
            User.username.ilike(pattern),
            User.first_name.ilike(pattern),
            User.last_name.ilike(pattern),
            cast(User.telegram_id, db.String).ilike(pattern),
        ))
        query = query.filter(or_(
            ActivityLog.user_id.in_(matching_users),
            ActivityLog.actor_label.ilike(pattern),
        ))
    if filters['event']:
        query = query.filter(ActivityLog.action == filters['event'])
    if filters['entity']:
        query = query.filter(ActivityLog.entity_type == filters['entity'])
    if filters['impersonation']:
        query = query.filter(ActivityLog.impersonation_session_id.isnot(None))
    date_from = _filter_datetime(filters['date_from'])
    date_to = _filter_datetime(filters['date_to'], end=True)
    if date_from:
        query = query.filter(ActivityLog.created_at >= date_from)
    if date_to:
        query = query.filter(ActivityLog.created_at < date_to)
    return query, filters


def _distinct_log_values(column):
    return [value for (value,) in db.session.query(column).filter(column.isnot(None)).distinct().order_by(column.asc()).all()]


def _csv_response(header, rows, filename):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    for row in rows:
        _write_csv_row(writer, row)
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _format_timestamp(value):
    return value.strftime('%Y-%m-%d %H:%M:%S') if value else ''


def _csv_safe(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def _write_csv_row(writer, values):
    writer.writerow([_csv_safe(value) for value in values])


SETTINGS_GROUPS = (
    {
        'title': 'Доступ',
        'kicker': 'Вход и регистрация',
        'fields': (
            {'key': 'telegram_login_enabled', 'type': 'bool', 'label': 'Вход через Telegram',
             'help': 'Показывает кнопку входа через Telegram и открывает Mini App. Работает, только если в окружении заданы токен и имя бота.'},
            {'key': 'registration_enabled', 'type': 'bool', 'label': 'Регистрация новых пользователей',
             'help': 'Создаёт аккаунт при первом входе через Telegram или Google. Если выключено, войти могут только уже существующие пользователи.'},
        ),
    },
    {
        'title': 'Долги',
        'kicker': 'Ограничения и архив',
        'fields': (
            {'key': 'debt_limit_per_user', 'type': 'int', 'min': 1, 'max': 1000, 'label': 'Лимит долгов на пользователя',
             'help': 'Сколько активных долгов можно завести в приложении и через Telegram-бота.'},
            {'key': 'archive_enabled', 'type': 'bool', 'label': 'Архив долгов',
             'help': 'Показывает раздел «Архив» и кнопки «В архив». Если выключить, архивные долги остаются в базе, но раздел и кнопки скрыты.'},
        ),
    },
    {
        'title': 'Сроки и напоминания',
        'kicker': 'Ближайшие платежи',
        'fields': (
            {'key': 'payment_warning_days', 'type': 'int', 'min': 0, 'max': 365, 'label': 'Предупреждать о платеже за, дней',
             'help': 'Карточка долга подсвечивается жёлтым, и Telegram-бот присылает напоминание за это число дней до платежа.'},
            {'key': 'urgent_payment_days', 'type': 'int', 'min': 0, 'max': 365, 'label': 'Считать платёж срочным за, дней',
             'help': 'Карточка подсвечивается оранжевым и получает бейдж «N дн.». Не больше срока предупреждения.'},
        ),
    },
    {
        'title': 'Администрирование',
        'kicker': 'Поддержка и аудит',
        'fields': (
            {'key': 'impersonation_timeout_minutes', 'type': 'int', 'min': 5, 'max': 120, 'label': 'Вход от имени пользователя, минут',
             'help': 'Через сколько минут сеанс входа от имени пользователя завершится и вернёт вас в административную панель.'},
            {'key': 'export_enabled', 'type': 'bool', 'label': 'Аудит-выгрузка',
             'help': 'Разрешает супер-администраторам скачивать учётные записи и журнал действий в CSV.'},
        ),
    },
    {
        'title': 'Оформление',
        'kicker': 'Интерфейс',
        'fields': (
            {'key': 'app_name', 'type': 'text', 'max': 80, 'label': 'Название приложения',
             'help': 'Показывается рядом со знаком в шапке приложения и в Mini App.'},
        ),
    },
)
SETTINGS_FIELDS = {field['key']: field for group in SETTINGS_GROUPS for field in group['fields']}


def _setting_default(key):
    if key == 'telegram_login_enabled':
        # Until the admin saves it, the effective value comes from the environment.
        return 'true' if current_app.config.get('TELEGRAM_LOGIN_ENABLED') else 'false'
    return DEFAULT_SETTINGS[key]


def _validated_settings(form):
    values = {}
    for key, field in SETTINGS_FIELDS.items():
        raw = str(form.get(key, '')).strip()
        if field['type'] == 'bool':
            values[key] = 'true' if raw.lower() in ('on', '1', 'true', 'yes') else 'false'
        elif field['type'] == 'int':
            try:
                number = int(raw)
            except ValueError:
                raise ValueError(f'«{field["label"]}»: значение должно быть целым числом.')
            if not field['min'] <= number <= field['max']:
                raise ValueError(f'«{field["label"]}»: значение должно быть от {field["min"]} до {field["max"]}.')
            values[key] = str(number)
        else:
            if not raw or len(raw) > field['max']:
                raise ValueError(f'«{field["label"]}»: от 1 до {field["max"]} символов.')
            values[key] = raw

    if int(values['urgent_payment_days']) > int(values['payment_warning_days']):
        raise ValueError('Срок срочного платежа не может превышать срок предупреждения.')
    return values


ROLE_LABELS = {'user': 'Пользователь', 'admin': 'Администратор', 'superadmin': 'Супер-администратор'}
ROLE_ACTIONS = {
    'user': 'Снял административные права',
    'admin': 'Назначил администратора',
    'superadmin': 'Назначил супер-администратора',
}


def _can_manage_access(target):
    """Superadmins manage everyone; ordinary admins manage only plain users."""
    return current_user.is_superadmin or target.role == 'user'


def _deny(message):
    flash(message, 'warning')
    db.session.rollback()
    return 'denied'


def _log_user_action(action, target):
    record_activity(
        action, current_user, entity_type='user', entity_id=target.id,
        description=f'Пользователь {target.username or target.telegram_id}',
        ip_address=client_ip(), user_agent=request.headers.get('User-Agent'),
    )


def _apply_user_action(user, form):
    """Apply one management action from the user card. Returns saved/deleted/denied."""
    action = form.get('action')
    is_self = str(user.id) == str(current_user.get_id())

    if action in ('block', 'unblock'):
        if is_self:
            return _deny('Нельзя изменить блокировку своей учётной записи.')
        if not _can_manage_access(user):
            return _deny('Администратор может блокировать только обычных пользователей.')
        if action == 'block':
            if _is_last_superadmin(user):
                return _deny('Нельзя заблокировать последнего супер-администратора.')
            user.is_blocked = True
            _log_user_action('Заблокировал пользователя', user)
        else:
            user.is_blocked = False
            _log_user_action('Разблокировал пользователя', user)
        return 'saved'

    if action == 'set_role':
        new_role = form.get('role')
        if not current_user.is_superadmin:
            return _deny('Менять роли может только супер-администратор.')
        if new_role not in ROLE_LABELS:
            return _deny('Выберите роль из списка.')
        if new_role == user.role:
            return _deny('Роль не изменилась.')
        if user.is_superadmin and _is_last_superadmin(user):
            return _deny('Нельзя понизить последнего супер-администратора.')
        user.role = new_role
        _log_user_action(ROLE_ACTIONS[new_role], user)
        return 'saved'

    if action == 'delete':
        if not current_user.is_superadmin:
            return _deny('Удалять пользователей может только супер-администратор.')
        if is_self:
            return _deny('Нельзя удалить свою учётную запись.')
        if _is_last_superadmin(user):
            return _deny('Нельзя удалить последнего супер-администратора.')
        expected = _deletion_confirmation(user)
        if str(form.get('confirm_username', '')).strip() != expected:
            return _deny(f'Для удаления введите «{expected}» в поле подтверждения.')
        _log_user_action('Удалил пользователя', user)
        db.session.delete(user)
        db.session.commit()
        return 'deleted'

    return _deny('Неизвестное действие.')


def _deletion_confirmation(user):
    return user.username or str(user.telegram_id)


def _dictionary_duplicate(dictionary_type, value):
    # Compared in Python: SQLite's lower() only folds ASCII, not Cyrillic.
    wanted = value.casefold()
    existing = db.session.query(DictionaryEntry.value).filter(DictionaryEntry.dictionary_type == dictionary_type)
    return any(item.casefold() == wanted for (item,) in existing)


def init_app(app):
    @app.context_processor
    def inject_admin_navigation_helpers():
        def admin_page_url(page):
            arguments = request.args.to_dict()
            arguments['page'] = page
            return url_for(request.endpoint, **arguments)

        return {'admin_page_url': admin_page_url}

    @app.route('/admin')
    @admin_required
    def admin_dashboard():
        error_message = None
        system_state = 'healthy'
        empty = '—'
        stats = dict.fromkeys(
            ('users', 'new_7', 'new_30', 'active_users', 'blocked_users', 'impersonations', 'logs'), empty,
        )
        recent_logs = []
        try:
            now = utc_now().replace(tzinfo=None)
            stats = {
                'users': User.query.count(),
                'new_7': User.query.filter(User.created_at >= now - timedelta(days=7)).count(),
                'new_30': User.query.filter(User.created_at >= now - timedelta(days=30)).count(),
                'active_users': User.query.filter_by(is_blocked=False).count(),
                'blocked_users': User.query.filter_by(is_blocked=True).count(),
                'impersonations': impersonation.active_count(),
                'logs': ActivityLog.query.count(),
            }
            recent_logs = ActivityLog.query.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).limit(5).all()
        except SQLAlchemyError:
            db.session.rollback()
            system_state = 'warning'
            error_message = 'База данных недоступна: показатели и события временно не загружены.'

        update_status = None
        if current_user.is_superadmin and current_app.config.get('SERVER_UPDATE_ENABLED'):
            # The overview only reads the status file; the update page talks to the helper.
            update_status = read_update_status(current_app.config)

        return render_template(
            'admin_dashboard.html',
            stats=stats,
            error_message=error_message,
            recent_logs=recent_logs,
            system_state=system_state,
            app_version=current_app.config.get('APP_VERSION', 'unknown'),
            update_enabled=bool(current_app.config.get('SERVER_UPDATE_ENABLED')),
            update_status=update_status,
            show_test_impersonation=(
                current_user.is_superadmin and _test_impersonation_enabled()
            ),
        )

    @app.route('/admin/server-update')
    @superadmin_required
    def admin_server_update():
        enabled = bool(current_app.config.get('SERVER_UPDATE_ENABLED'))
        latest_release = None
        release_error = None
        readiness = {'static': None, 'integration': None, 'error': None}
        if enabled:
            try:
                latest_release = fetch_latest_release(current_app.config)
            except ServerUpdateError as exc:
                release_error = str(exc)
            try:
                readiness['static'] = check_update_readiness(current_app.config)
                readiness['integration'] = check_update_readiness(
                    current_app.config, integration=True
                )
            except ServerUpdateError as exc:
                readiness['error'] = str(exc)

        current_release = get_current_release(current_app.config)
        update_status = reconcile_update_status(
            get_update_status(current_app.config), current_release
        )
        is_active = update_is_active(current_app.config, update_status)
        is_current = bool(
            latest_release
            and current_release_has_tag(current_release, latest_release.get('tag'))
        )
        return render_template(
            'admin_server_update.html',
            enabled=enabled,
            repository=current_app.config.get('SERVER_UPDATE_REPOSITORY'),
            current_release=current_release,
            latest_release=latest_release,
            release_error=release_error,
            update_status=update_status,
            is_active=is_active,
            is_current=is_current,
            readiness=readiness,
        )

    @app.route('/admin/server-update/status')
    @superadmin_required
    def admin_server_update_status():
        current_release = get_current_release(current_app.config)
        status = reconcile_update_status(
            get_update_status(current_app.config), current_release
        ) or {
            'state': 'idle',
            'message': 'Обновление ещё не запускалось.',
        }
        response = jsonify({
            'state': str(status.get('state') or 'unknown'),
            'tag': str(status.get('tag') or ''),
            'message': str(status.get('message') or ''),
            'operation_id': str(status.get('operation_id') or ''),
            'error_code': str(status.get('error_code') or ''),
            'updated_at': str(status.get('updated_at') or ''),
        })
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/admin/server-update/apply', methods=['POST'])
    @superadmin_required
    def admin_server_update_apply():
        requested_tag = request.form.get('tag', '')
        try:
            release = request_server_update(
                current_app.config,
                requested_tag,
                requested_by=current_user.id,
            )
        except ServerUpdateError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('admin_server_update'))
        except OSError:
            current_app.logger.exception('Failed to create the server update request')
            flash('Не удалось подготовить запрос обновления на сервере.', 'danger')
            return redirect(url_for('admin_server_update'))

        try:
            record_activity(
                'Запустил обновление сервера',
                current_user,
                entity_type='server_update',
                description=f'Запрошена установка релиза {release["tag"]}',
                ip_address=client_ip(),
                user_agent=request.headers.get('User-Agent'),
            )
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Failed to record the server update request')

        flash(
            f'Обновление до {release["tag"]} передано серверу. '
            'Страница будет показывать ход выполнения.',
            'success',
        )
        return redirect(url_for('admin_server_update'))

    @app.route('/admin/settings', methods=['GET', 'POST'])
    @superadmin_required
    def admin_settings():
        error_message = None

        if request.method == 'POST':
            try:
                values = _validated_settings(request.form)
                changed = [key for key, value in values.items() if str(get_setting(key, _setting_default(key))) != value]
                for key, value in values.items():
                    set_setting(key, value, commit=False)
                db.session.commit()
                record_activity(
                    'Изменил настройки приложения', current_user, entity_type='setting',
                    description='Изменены: ' + (', '.join(changed) if changed else 'без изменений'),
                )
                flash('Настройки сохранены.', 'success')
                return redirect(url_for('admin_settings'))
            except ValueError as exc:
                db.session.rollback()
                error_message = str(exc)
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception('Failed to save application settings')
                error_message = 'Не удалось сохранить настройки.'

        if request.method == 'POST' and error_message:
            settings = {
                key: ('true' if request.form.get(key) else 'false') if field['type'] == 'bool' else request.form.get(key, '')
                for key, field in SETTINGS_FIELDS.items()
            }
        else:
            settings = {key: str(get_setting(key, _setting_default(key))) for key in SETTINGS_FIELDS}
        return render_template(
            'admin_settings.html',
            settings=settings,
            settings_groups=SETTINGS_GROUPS,
            contacts=load_contacts(),
            contact_types=CONTACT_TYPES,
            contact_icons=CONTACT_ICONS,
            contact_locations=CONTACT_LOCATIONS,
            error_message=error_message,
        )

    @app.route('/admin/settings/contacts', methods=['POST'])
    @superadmin_required
    def admin_settings_contacts():
        action = str(request.form.get('action') or 'save')
        contact_id = str(request.form.get('contact_id') or '').strip()
        try:
            if action == 'delete':
                delete_contact(contact_id)
                activity = 'Удалил публичный контакт'
            elif action == 'save':
                save_contact({
                    'type': request.form.get('type'),
                    'label': request.form.get('label'),
                    'address': request.form.get('address'),
                    'icon': request.form.get('icon'),
                    'published': bool(request.form.get('published')),
                    'locations': request.form.getlist('locations'),
                    'order': request.form.get('order', '0'),
                }, contact_id=contact_id or None)
                activity = 'Изменил публичные контакты'
            else:
                raise ContactValidationError('Неподдерживаемое действие.')
            record_activity(
                activity,
                current_user,
                entity_type='setting',
                description='Обновлена публикация контактов и ссылок',
            )
            flash('Контакты сохранены.', 'success')
            return redirect(url_for('admin_settings'))
        except ContactValidationError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
            return redirect(url_for('admin_settings'))
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Failed to save public contacts')
            flash('Не удалось сохранить контакты.', 'danger')
            return redirect(url_for('admin_settings'))

    @app.route('/admin/dictionaries', methods=['GET', 'POST'])
    @superadmin_required
    def admin_dictionaries():
        error_message = None
        form_values = {'dictionary_type': '', 'value': ''}
        if request.method == 'POST':
            form_values = {
                'dictionary_type': request.form.get('dictionary_type', ''),
                'value': request.form.get('value', ''),
            }
            try:
                dictionary_type = form_values['dictionary_type']
                value = normalize_dictionary_value(dictionary_type, form_values['value'])
                if _dictionary_duplicate(dictionary_type, value):
                    raise ValueError('Такое значение уже есть в этом справочнике.')
                db.session.add(DictionaryEntry(dictionary_type=dictionary_type, value=value))
                db.session.commit()
                record_activity('Добавил элемент справочника', current_user, entity_type=dictionary_type, description=value)
                flash('Элемент справочника добавлен.', 'success')
                return redirect(url_for('admin_dictionaries'))
            except ValueError as exc:
                db.session.rollback()
                error_message = str(exc)
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception('Failed to save dictionary entry')
                error_message = 'Не удалось сохранить элемент справочника: база данных недоступна.'

        try:
            entries = DictionaryEntry.query.order_by(DictionaryEntry.dictionary_type.asc(), DictionaryEntry.value.asc()).all()
        except SQLAlchemyError:
            db.session.rollback()
            entries = []
            error_message = error_message or 'База данных недоступна, справочники временно не загружены.'
        grouped = {key: [] for key, _ in DICTIONARY_TYPES}
        for entry in entries:
            grouped.setdefault(entry.dictionary_type, []).append(entry)
        return render_template(
            'admin_dictionaries.html',
            types=DICTIONARY_TYPES,
            type_help=DICTIONARY_HELP,
            grouped=grouped,
            total=len(entries),
            form_values=form_values,
            error_message=error_message,
        )

    @app.route('/admin/dictionaries/<int:entry_id>/update', methods=['POST'])
    @superadmin_required
    def admin_update_dictionary_entry(entry_id):
        entry = db.session.get(DictionaryEntry, entry_id)
        if entry is None:
            flash('Элемент справочника не найден: возможно, он уже удалён.', 'warning')
            return redirect(url_for('admin_dictionaries'))
        action = request.form.get('action', 'rename')
        try:
            if action == 'toggle':
                entry.is_active = not entry.is_active
                message = 'Элемент включён.' if entry.is_active else 'Элемент выключен: он больше не предлагается в формах.'
                log_action = 'Изменил элемент справочника'
                description = f'{entry.value}: {"включён" if entry.is_active else "выключен"}'
            else:
                previous = entry.value
                value = normalize_dictionary_value(entry.dictionary_type, request.form.get('value'))
                if value != previous and _dictionary_duplicate(entry.dictionary_type, value):
                    raise ValueError('Такое значение уже есть в этом справочнике.')
                entry.value = value
                message = 'Элемент справочника сохранён.'
                log_action = 'Изменил элемент справочника'
                description = f'{previous} → {value}'
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'warning')
            return redirect(url_for('admin_dictionaries'))
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Failed to update dictionary entry')
            flash('Не удалось сохранить элемент справочника: база данных недоступна.', 'danger')
            return redirect(url_for('admin_dictionaries'))
        record_activity(log_action, current_user, entity_type=entry.dictionary_type, entity_id=entry.id, description=description)
        flash(message, 'success')
        return redirect(url_for('admin_dictionaries'))

    @app.route('/admin/dictionaries/<int:entry_id>/delete', methods=['POST'])
    @superadmin_required
    def admin_delete_dictionary_entry(entry_id):
        entry = db.session.get(DictionaryEntry, entry_id)
        if entry is None:
            flash('Элемент справочника не найден: возможно, он уже удалён.', 'warning')
            return redirect(url_for('admin_dictionaries'))
        entry_type, entry_value = entry.dictionary_type, entry.value
        try:
            db.session.delete(entry)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Failed to delete dictionary entry')
            flash('Не удалось удалить элемент справочника: база данных недоступна.', 'danger')
            return redirect(url_for('admin_dictionaries'))
        record_activity('Удалил элемент справочника', current_user, entity_type=entry_type, entity_id=entry_id, description=entry_value)
        flash('Элемент справочника удалён.', 'success')
        return redirect(url_for('admin_dictionaries'))

    @app.route('/admin/users')
    @admin_required
    def admin_users():
        role_filter = request.args.get('role')
        status_filter = request.args.get('status')
        search_query = (request.args.get('q') or '').strip()
        sort = request.args.get('sort', 'newest')

        users_query = User.query
        if role_filter in ('user', 'admin', 'superadmin'):
            users_query = users_query.filter_by(role=role_filter)
        if status_filter == 'blocked':
            users_query = users_query.filter_by(is_blocked=True)
        elif status_filter == 'active':
            users_query = users_query.filter_by(is_blocked=False)
        elif status_filter == 'impersonated':
            users_query = users_query.filter(User.id.in_(impersonation.active_target_ids_query()))
        if search_query:
            users_query = users_query.filter(
                (User.username.ilike(f'%{search_query}%')) |
                (User.first_name.ilike(f'%{search_query}%')) |
                (User.last_name.ilike(f'%{search_query}%')) |
                (cast(User.telegram_id, db.String).ilike(f'%{search_query}%'))
            )

        sort_options = {
            'newest': (User.created_at.desc(), User.id.desc()),
            'oldest': (User.created_at.asc(), User.id.asc()),
            'name': (User.first_name.asc(), User.last_name.asc(), User.username.asc()),
            'activity': (User.login_count.desc(), User.created_at.desc()),
            'role': (User.role.desc(), User.created_at.desc()),
        }
        if sort not in sort_options:
            sort = 'newest'
        pagination = users_query.order_by(*sort_options[sort]).paginate(
            page=_page_number(),
            per_page=20,
            error_out=False,
        )
        return render_template(
            'admin_users.html',
            users=pagination.items,
            pagination=pagination,
            role_filter=role_filter,
            status_filter=status_filter,
            q=search_query,
            sort=sort,
            show_test_impersonation=(
                current_user.is_superadmin and _test_impersonation_enabled()
            ),
        )

    @app.route('/admin/impersonate/test', methods=['POST'])
    @superadmin_required
    def admin_impersonate_test():
        if not _test_impersonation_enabled():
            abort(404)
        try:
            test_user = User.query.filter_by(username='test').first()
            if not test_user:
                test_user = User(
                    telegram_id=-999999999,
                    username='test',
                    first_name='test',
                    last_name=None,
                    auth_date=utc_now(),
                    role='user',
                    is_blocked=False,
                    login_count=0,
                )
                db.session.add(test_user)
                db.session.commit()
            if test_user.is_blocked:
                test_user.is_blocked = False
                db.session.commit()
            # The test account needs no reason and allows changes, but it goes
            # through the same audited, time-limited session as any other user.
            impersonation.start(current_user, test_user, read_only=False, test_user=True)
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Failed to start test-user impersonation')
            flash('Не удалось начать сеанс тестового пользователя: база данных недоступна.', 'danger')
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('index'))

    @app.route('/admin/impersonate/<int:user_id>', methods=['POST'])
    @superadmin_required
    def admin_impersonate_user(user_id):
        user = db.session.get(User, user_id) or abort(404)
        try:
            impersonation.ensure_can_impersonate(current_user, user)
            reason = impersonation.validate_reason(request.form.get('reason'))
        except impersonation.ImpersonationError as exc:
            flash(str(exc), 'warning')
            return redirect(url_for('admin_user_detail', user_id=user.id))
        read_only = request.form.get('allow_changes') not in ('on', '1', 'true', 'yes')
        try:
            impersonation.start(current_user, user, reason=reason, read_only=read_only)
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Failed to start impersonation')
            flash('Не удалось начать сеанс: база данных недоступна.', 'danger')
            return redirect(url_for('admin_user_detail', user_id=user.id))
        return redirect(url_for('index'))

    @app.route('/admin/users/<int:user_id>', methods=['GET', 'POST'])
    @admin_required
    def admin_user_detail(user_id):
        user = db.session.get(User, user_id) or abort(404)
        if request.method == 'POST':
            outcome = _apply_user_action(user, request.form)
            if outcome == 'deleted':
                flash('Пользователь удалён. Записи журнала о нём сохранены.', 'success')
                return redirect(url_for('admin_users'))
            if outcome == 'saved':
                flash('Изменения сохранены.', 'success')
            return redirect(url_for('admin_user_detail', user_id=user.id))

        recent_actions = ActivityLog.query.filter(
            or_(
                ActivityLog.user_id == user.id,
                and_(ActivityLog.entity_type == 'user', ActivityLog.entity_id == user.id),
            )
        ).order_by(ActivityLog.created_at.desc()).limit(20).all()
        return render_template(
            'admin_user_detail.html',
            user=user,
            recent_actions=recent_actions,
            impersonation_timeout=impersonation.timeout_minutes(),
            can_manage_access=_can_manage_access(user) and str(user.id) != str(current_user.get_id()),
            role_labels=ROLE_LABELS,
            delete_confirmation=_deletion_confirmation(user),
        )

    @app.route('/admin/logs')
    @admin_required
    def admin_logs():
        logs_query, filters = _activity_log_query(request.args)

        pagination = logs_query.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).paginate(
            page=_page_number(),
            per_page=30,
            error_out=False,
        )
        filtered_user = db.session.get(User, filters['user']) if filters['user'] else None
        return render_template(
            'admin_logs.html',
            logs=pagination.items,
            pagination=pagination,
            event_values=_distinct_log_values(ActivityLog.action),
            entity_values=_distinct_log_values(ActivityLog.entity_type),
            filters=filters,
            filtered_user=filtered_user,
            has_filters=any(filters.values()),
        )

    @app.route('/admin/system')
    @superadmin_required
    def admin_system():
        database_state = 'healthy'
        try:
            db.session.execute(text('SELECT 1'))
        except SQLAlchemyError:
            db.session.rollback()
            database_state = 'warning'

        telegram_states = telegram_feature_states(current_app.config)
        feature_states = (
            ('Google OAuth', current_app.config.get('GOOGLE_LOGIN_ENABLED')),
            ('Обновление сервера', current_app.config.get('SERVER_UPDATE_ENABLED')),
        )
        return render_template(
            'admin_system.html',
            database_state=database_state,
            feature_states=feature_states,
            telegram_states=telegram_states,
            app_version=current_app.config.get('APP_VERSION', 'unknown'),
            environment=current_app.config.get('ENVIRONMENT', 'unknown'),
            timezone=current_app.config.get('APP_TIMEZONE', 'UTC'),
            database_engine=current_app.config.get('DB_ENGINE', 'unknown'),
        )

    @app.route('/admin/export')
    @superadmin_required
    def admin_export():
        if not _setting_enabled('export_enabled', True):
            abort(403)
        _, filters = _activity_log_query(request.args)
        try:
            counts = {
                'accounts': User.query.count(),
                'events': ActivityLog.query.count(),
            }
            event_values = _distinct_log_values(ActivityLog.action)
            error_message = None
        except SQLAlchemyError:
            db.session.rollback()
            counts = {'accounts': '—', 'events': '—'}
            event_values = []
            error_message = 'База данных недоступна, выгрузка временно невозможна.'
        return render_template(
            'admin_export.html',
            counts=counts,
            event_values=event_values,
            filters=filters,
            error_message=error_message,
        )

    @app.route('/admin/export/accounts.csv', methods=['POST'])
    @superadmin_required
    def admin_export_accounts():
        if not _setting_enabled('export_enabled', True):
            abort(403)
        users = User.query.order_by(User.id.asc()).all()
        record_activity(
            'Выгрузил данные аудита', current_user, entity_type='export',
            description=f'accounts.csv: {len(users)} учётных записей',
            ip_address=client_ip(), user_agent=request.headers.get('User-Agent'),
        )
        return _csv_response(
            ['id', 'telegram_id', 'username', 'first_name', 'last_name', 'role', 'status', 'login_count', 'created_at'],
            (
                [
                    user.id, user.telegram_id, user.username, user.first_name, user.last_name, user.role,
                    'blocked' if user.is_blocked else 'active', user.login_count, _format_timestamp(user.created_at),
                ]
                for user in users
            ),
            'accounts.csv',
        )

    @app.route('/admin/export/activity-log.csv', methods=['POST'])
    @superadmin_required
    def admin_export_activity_log():
        if not _setting_enabled('export_enabled', True):
            abort(403)
        logs_query, filters = _activity_log_query(request.form)
        logs = logs_query.order_by(ActivityLog.created_at.asc(), ActivityLog.id.asc()).all()
        applied = ', '.join(f'{key}={value}' for key, value in filters.items() if value) or 'без фильтров'
        record_activity(
            'Выгрузил данные аудита', current_user, entity_type='export',
            description=f'activity_log.csv: {len(logs)} событий ({applied})',
            ip_address=client_ip(), user_agent=request.headers.get('User-Agent'),
        )
        return _csv_response(
            ['id', 'created_at', 'user_id', 'username', 'action', 'entity_type', 'entity_id', 'description', 'ip_address'],
            (
                [
                    log.id, _format_timestamp(log.created_at), log.user_id,
                    log.user.username if log.user else '', log.action, log.entity_type, log.entity_id,
                    log.description, log.ip_address,
                ]
                for log in logs
            ),
            'activity_log.csv',
        )
