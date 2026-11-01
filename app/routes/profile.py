from flask import render_template, request, redirect, url_for, abort
from flask_login import login_required, current_user
from app.models import User
from extensions import db


def init_app(app):
    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        if request.method == 'POST':
            if not isinstance(current_user._get_current_object(), User) or not current_user.telegram_id:
                abort(403)
            current_user.vehicle_notifications_enabled = request.form.get('vehicle_notifications_enabled') == 'on'
            db.session.commit()
            return redirect(url_for('profile'))
        return render_template('profile.html')
