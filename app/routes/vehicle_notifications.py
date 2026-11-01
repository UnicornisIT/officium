from flask import abort, jsonify, request
from app.services.vehicle_notification_service import send_due_notifications


def init_app(app, csrf):
    @app.post('/internal/vehicle-notifications/run')
    @csrf.exempt
    def vehicle_notifications_run():
        if not app.config.get('VEHICLE_NOTIFICATIONS_ENABLED') or not app.config.get('VEHICLES_ENABLED'):
            abort(404)
        # REMOTE_ADDR comes from the WSGI peer; forwarded headers are not trusted.
        if request.environ.get('REMOTE_ADDR') != '127.0.0.1':
            abort(403)
        return jsonify(send_due_notifications())
