from flask import abort, current_app, jsonify, request

from app.services.telegram_bot_service import (
    answer_telegram_callback_query,
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
        if not expected_secret or not provided_secret or provided_secret != expected_secret:
            abort(403)

        update = request.get_json(silent=True)
        if not isinstance(update, dict):
            return jsonify({'ok': True})

        result = handle_telegram_update_once(update)
        if result.callback_query_id:
            try:
                answer_telegram_callback_query(result.callback_query_id, result.callback_answer_text)
            except Exception:
                current_app.logger.warning('Telegram callback answer failed.')
        if result.chat_id is not None and result.reply_text:
            try:
                send_telegram_message(result.chat_id, result.reply_text, reply_markup=result.reply_markup)
            except Exception:
                current_app.logger.warning('Telegram webhook reply delivery failed.')
        return jsonify({'ok': True})

    if csrf is not None:
        csrf.exempt(telegram_webhook)
