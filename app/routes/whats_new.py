"""Show the notes of the running release once per user and record that they saw them."""

from flask import abort, current_app, jsonify, redirect, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from app.models import User
from app.services.whats_new_service import normalize_version, pending_release_notes


def _account_user():
    """The signed-in database account, or ``None`` for service and admin-only logins."""
    user = current_user._get_current_object() if getattr(current_user, 'is_authenticated', False) else None
    return user if isinstance(user, User) else None


def _wants_json():
    return request.is_json or request.accept_mimetypes.best == 'application/json'


def _safe_return_path(value):
    value = str(value or '')
    if value.startswith('/') and not value.startswith('//') and '\\' not in value:
        return value
    return url_for('index')


def init_app(app):
    @app.context_processor
    def inject_whats_new():
        # The panel and a session on behalf of a user never show (or consume) the notes.
        if request.path.startswith('/admin') or session.get('original_admin_id'):
            return {'whats_new': None}
        user = _account_user()
        if user is None:
            return {'whats_new': None}
        return {'whats_new': pending_release_notes(user, app.config.get('APP_VERSION'))}

    @app.route('/whats-new/seen', methods=['POST'])
    @login_required
    def whats_new_seen():
        user = _account_user()
        if user is None or session.get('original_admin_id'):
            abort(403)
        version = normalize_version(current_app.config.get('APP_VERSION'))
        if version and user.last_seen_release != version:
            try:
                user.last_seen_release = version
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception('Could not record the acknowledged release')
                if _wants_json():
                    return jsonify({'success': False, 'error': 'Не удалось сохранить отметку.'}), 500
                return redirect(_safe_return_path(request.form.get('next')), code=303)
        if _wants_json():
            return jsonify({'success': True, 'version': version})
        return redirect(_safe_return_path(request.form.get('next')), code=303)
