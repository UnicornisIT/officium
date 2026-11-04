import secrets

from flask import abort, current_app, jsonify, request

from app.services.telegram_bot_service import (
    answer_telegram_callback_query,
    deliver_telegram_result,
    handle_telegram_update_once,
    send_telegram_message,
)


def init_app(app, csrf=None):
    @app.route('/telegram/webhook', methods=['POST'])
    def telegram_webhook():
        if not current_app.config.get('TELEGRAM_BOT_ENABLED', False):
            abort(404)

        expected_secret = current_app.config.get('TELEGRAM_WEBHOOK_SECRET', '')
        provided_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if (
            not expected_secret
            or not provided_secret
            or not secrets.compare_digest(str(provided_secret), str(expected_secret))
        ):
            abort(403)

        update = request.get_json(silent=True)
        if not isinstance(update, dict):
            return jsonify({'ok': True})

        result = handle_telegram_update_once(update)
        deliver_telegram_result(result, sender=send_telegram_message,
                                callback_answerer=answer_telegram_callback_query)
        return jsonify({'ok': True})

    if csrf is not None:
        csrf.exempt(telegram_webhook)
