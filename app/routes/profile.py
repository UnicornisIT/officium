from flask import render_template
from flask_login import login_required


def init_app(app):
    @app.route('/profile')
    @login_required
    def profile():
        return render_template('profile.html')
