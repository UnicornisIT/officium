from ipaddress import ip_address

from flask import abort, jsonify, request
from app.services.vehicle_notification_service import (
    send_due_notifications,
    notifications_enabled,
)


def init_app(app, csrf):
    @app.post("/internal/vehicle-notifications/run")
    @csrf.exempt
    def vehicle_notifications_run():
        if not notifications_enabled():
            abort(404)
        # REMOTE_ADDR comes from the WSGI peer; forwarded headers are not trusted.
        try:
            is_loopback = ip_address(request.environ.get("REMOTE_ADDR")).is_loopback
        except ValueError:
            abort(403)
        if not is_loopback:
            abort(403)
        return jsonify(send_due_notifications())
