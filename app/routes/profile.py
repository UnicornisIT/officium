from flask import render_template, request, redirect, url_for, abort, session
from flask_login import login_required, current_user
from sqlalchemy.exc import SQLAlchemyError
from app.services import personal_settings_service as settings
from app.services.vehicle_notification_service import notifications_enabled
from app.utils import telegram_feature_states
from extensions import db


def init_app(app):
    @app.context_processor
    def amount_privacy_context():
        user = current_user._get_current_object()
        enabled = settings.persistent_user(user) and not request.path.startswith('/admin')
        return dict(amount_privacy_available=enabled,
                    amounts_initially_hidden=enabled and bool(user.hide_amounts_on_open))

    def page(*, errors=None, test_result=None, status=200):
        user = current_user._get_current_object()
        zone, clock = settings.delivery_preferences(user)
        return (
            render_template(
                'profile.html',
                persistent_account=settings.persistent_user(user),
                telegram_account=settings.has_telegram(user),
                notification_zone=zone,
                notification_clock=clock,
                timezone_choices=settings.timezone_choices(),
                bot_url=settings.bot_url(),
                automated_notifications_enabled=notifications_enabled(),
                bot_configured=bool(app.config.get('TELEGRAM_BOT_TOKEN')),
                bot_commands_enabled=telegram_feature_states(app.config)['bot'][
                    'effective'
                ],
                profile_errors=errors or {},
                test_result=test_result or session.pop('profile_test_result', None),
                saved_section=session.pop('profile_saved_section', None),
            ),
            status,
        )

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        if request.method == 'POST':
            user = current_user._get_current_object()
            if not settings.persistent_user(user) or user.is_blocked:
                abort(403)
            section = request.form.get('section', 'notifications')
            try:
                saved = settings.save(user, request.form)
            except ValueError as exc:
                db.session.rollback()
                return page(errors={section if section in ('notifications', 'privacy', 'monthly_reminders') else 'general': str(exc)}, status=400)
            except SQLAlchemyError:
                db.session.rollback()
                return page(errors={section: 'Не удалось сохранить настройки. Повторите попытку.'}, status=503)
            session['profile_saved_section'] = saved
            return redirect(url_for('profile'))
        return page()

    @app.post('/profile/test-notification')
    @login_required
    def profile_test_notification():
        try:
            ok, message, status = settings.test_notification(current_user._get_current_object())
        except SQLAlchemyError:
            db.session.rollback()
            return page(test_result=(False, 'Не удалось выполнить проверку. Повторите попытку позже.'), status=503)
        if ok:
            session['profile_test_result'] = (ok, message)
            return redirect(url_for('profile'), code=303)
        return page(test_result=(ok, message), status=status)
