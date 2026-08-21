from flask import render_template


def init_app(app):
    @app.route('/documentation')
    def documentation():
        return render_template('documentation.html')
