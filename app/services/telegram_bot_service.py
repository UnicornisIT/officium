import json
import re
import shlex
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Union

import requests
from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.models import Debt, Expense, Income, TelegramConversationState, TelegramProcessedUpdate, User
from app.services.finance_summary_service import get_finance_summary
from app.services.payment_service import add_payment
from extensions import db


MONEY = Decimal('0.01')

EXPENSE_LABELS = {
    'products': 'продукты',
    'transport': 'транспорт',
    'communication': 'связь',
    'rent': 'аренда',
    'loans': 'кредиты',
    'restaurants': 'рестораны и кафе',
    'entertainment': 'развлечения',
    'health': 'здоровье',
    'education': 'обучение',
    'clothing': 'одежда',
    'subscriptions': 'подписки',
    'other': 'другое',
}

INCOME_LABELS = {
    'salary': 'зарплата',
    'advance': 'аванс',
    'side_job': 'подработка',
    'debt_return': 'возврат долга',
    'bonus': 'премия',
    'scholarship': 'стипендия',
    'vacation_pay': 'отпускные',
    'other': 'другое',
}

DEBT_TYPE_LABELS = {
    'credit_card': 'кредитная карта',
    'consumer_credit': 'потребительский кредит',
    'mortgage': 'ипотека',
    'split': 'сплит',
}

PAYMENT_METHOD_LABELS = {
    'card': 'карта',
    'cash': 'наличные',
    'transfer': 'перевод',
    'other': 'другое',
}

COMMAND_ALIASES = {
    '/start': 'help',
    '/help': 'help',
    '/cancel': 'cancel',
    '/expense': 'expense',
    '/income': 'income',
    '/debt': 'debt',
    '/debts': 'debts',
    '/payment': 'payment',
    '/summary': 'summary',
    '/privacy': 'privacy',
    'помощь': 'help',
    'help': 'help',
    'privacy': 'privacy',
    'приватность': 'privacy',
    'отмена': 'cancel',
    'cancel': 'cancel',
    'расход': 'expense',
    'расходы': 'expense',
    'трата': 'expense',
    'потратил': 'expense',
    'минус': 'expense',
    'доход': 'income',
    'доходы': 'income',
    'зачисление': 'income',
    'плюс': 'income',
    'долг': 'debt',
    'долги': 'debts',
    'кредиты': 'debts',
    'платеж': 'payment',
    'оплата': 'payment',
    'погашение': 'payment',
    'итог': 'summary',
    'сводка': 'summary',
    'баланс': 'summary',
}

EXPENSE_ALIASES = {
    'product': 'products',
    'products': 'products',
    'продукт': 'products',
    'продукты': 'products',
    'еда': 'products',
    'магазин': 'products',
    'супермаркет': 'products',
    'транспорт': 'transport',
    'такси': 'transport',
    'метро': 'transport',
    'автобус': 'transport',
    'связь': 'communication',
    'телефон': 'communication',
    'интернет': 'communication',
    'аренда': 'rent',
    'квартира': 'rent',
    'кредит': 'loans',
    'кредиты': 'loans',
    'займ': 'loans',
    'кафе': 'restaurants',
    'ресторан': 'restaurants',
    'рестораны': 'restaurants',
    'кофе': 'restaurants',
    'развлечения': 'entertainment',
    'кино': 'entertainment',
    'здоровье': 'health',
    'аптека': 'health',
    'врач': 'health',
    'обучение': 'education',
    'учеба': 'education',
    'курс': 'education',
    'одежда': 'clothing',
    'подписка': 'subscriptions',
    'подписки': 'subscriptions',
    'другое': 'other',
    'прочее': 'other',
}

INCOME_ALIASES = {
    'salary': 'salary',
    'зарплата': 'salary',
    'зп': 'salary',
    'аванс': 'advance',
    'подработка': 'side_job',
    'фриланс': 'side_job',
    'freelance': 'side_job',
    'возврат': 'debt_return',
    'вернули': 'debt_return',
    'премия': 'bonus',
    'бонус': 'bonus',
    'стипендия': 'scholarship',
    'отпускные': 'vacation_pay',
    'другое': 'other',
    'прочее': 'other',
}

DEBT_TYPE_ALIASES = {
    'card': 'credit_card',
    'карта': 'credit_card',
    'кредитка': 'credit_card',
    'кредитная': 'credit_card',
    'потреб': 'consumer_credit',
    'потребкредит': 'consumer_credit',
    'кредит': 'consumer_credit',
    'займ': 'consumer_credit',
    'ипотека': 'mortgage',
    'mortgage': 'mortgage',
    'сплит': 'split',
    'рассрочка': 'split',
    'split': 'split',
}

PAYMENT_METHOD_ALIASES = {
    'card': 'card',
    'карта': 'card',
    'картой': 'card',
    'нал': 'cash',
    'наличные': 'cash',
    'cash': 'cash',
    'перевод': 'transfer',
    'transfer': 'transfer',
    'другое': 'other',
}

OPTION_ALIASES = {
    'date': 'date',
    'дата': 'date',
    'category': 'category',
    'категория': 'category',
    'кат': 'category',
    'method': 'payment_method',
    'метод': 'payment_method',
    'оплата': 'payment_method',
    'payment_method': 'payment_method',
    'банк': 'bank',
    'bank': 'bank',
    'продукт': 'product',
    'product': 'product',
    'тип': 'debt_type',
    'type': 'debt_type',
    'мин': 'minimum_payment',
    'минимальный': 'minimum_payment',
    'минимум': 'minimum_payment',
    'минималка': 'minimum_payment',
    'ставка': 'interest_rate',
    'процент': 'interest_rate',
    'rate': 'interest_rate',
    'долг': 'debt_id',
    'debt': 'debt_id',
    'id': 'debt_id',
    'досрочно': 'early',
    'early': 'early',
}

MAIN_MENU_KEYBOARD = {
    'keyboard': [
        [{'text': 'Расход'}, {'text': 'Доход'}, {'text': 'Платеж'}],
        [{'text': 'Долг'}, {'text': 'Долги'}, {'text': 'Итог'}],
    ],
    'resize_keyboard': True,
    'one_time_keyboard': False,
    'input_field_placeholder': 'Выберите действие',
}

HELP_TEXT = (
    'Выберите действие кнопкой ниже или напишите команду.\n\n'
    'Пошагово: Расход, Доход, Платеж, Долг.\n'
    'Быстрой строкой тоже можно:\n'
    'расход 850 продукты пятерочка\n'
    'доход 120000 зарплата работа\n'
    'платеж 5000 сбер\n\n'
    'Дату можно писать как 2026-07-29, 29.07, сегодня или вчера.\n'
    'Про приватность: /privacy'
)

PRIVACY_TEXT = (
    'Приватность Telegram-бота:\n'
    '- работаю только с уже существующим аккаунтом Officium по Telegram ID;\n'
    '- по умолчанию принимаю команды только в личном чате;\n'
    '- не сохраняю текст Telegram-сообщений в журнал действий;\n'
    '- для защиты от повторов храню только технический update_id и время обработки;\n'
    '- пошаговый ввод хранится временно и автоматически очищается по TTL;\n'
    '- не отправляйте в бот паспортные данные, документы, адреса и другую лишнюю персональную информацию.'
)

FLOW_COMMANDS = {'expense', 'income', 'debt', 'payment'}
CANCEL_WORDS = {'отмена', 'cancel', 'стоп', 'stop'}
CONFIRM_WORDS = {'да', 'сохранить', 'ok', 'okay', 'yes', 'confirm'}
REJECT_WORDS = {'нет', 'no', 'не', 'отмена', 'cancel'}
_RATE_LIMIT_BUCKETS = {}


@dataclass
class TelegramBotResult:
    chat_id: Optional[Union[int, str]]
    reply_text: Optional[str]
    reply_markup: Optional[dict] = None
    callback_query_id: Optional[str] = None
    callback_answer_text: Optional[str] = None
    ok: bool = True


def handle_telegram_update(update):
    context = _update_context(update)
    chat_id = context.get('chat_id')
    chat_type = context.get('chat_type')
    telegram_id = context.get('telegram_id')
    text = context.get('text')
    callback_data = context.get('callback_data')
    callback_query_id = context.get('callback_query_id')

    if not chat_id:
        return TelegramBotResult(chat_id=None, reply_text=None, callback_query_id=callback_query_id)
    if current_app.config.get('TELEGRAM_PRIVATE_CHAT_ONLY', True) and chat_type != 'private':
        return TelegramBotResult(
            chat_id=chat_id,
            reply_text='Для безопасности я работаю только в личном чате.',
            callback_query_id=callback_query_id,
            callback_answer_text='Только личный чат',
        )
    if telegram_id is None:
        return TelegramBotResult(
            chat_id=chat_id,
            reply_text='Не смог определить отправителя сообщения.',
            callback_query_id=callback_query_id,
        )

    try:
        telegram_id = int(telegram_id)
    except (TypeError, ValueError):
        return TelegramBotResult(
            chat_id=chat_id,
            reply_text='Не смог определить Telegram ID отправителя.',
            callback_query_id=callback_query_id,
        )

    if _is_rate_limited(telegram_id, chat_id):
        return TelegramBotResult(
            chat_id=chat_id,
            reply_text='Слишком много сообщений подряд. Попробуйте через минуту.',
            callback_query_id=callback_query_id,
        )

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return TelegramBotResult(
            chat_id=chat_id,
            reply_text='Я не нашел ваш аккаунт в Officium. Сначала войдите в приложение через Telegram.',
            callback_query_id=callback_query_id,
        )
    if user.is_blocked:
        return TelegramBotResult(
            chat_id=chat_id,
            reply_text='Ваш аккаунт заблокирован. Запись через Telegram недоступна.',
            callback_query_id=callback_query_id,
        )

    if callback_data:
        result = process_telegram_callback_result(user, chat_id, callback_data)
        result.callback_query_id = callback_query_id
        if result.callback_answer_text is None:
            result.callback_answer_text = 'Готово'
        return result

    if not text:
        return TelegramBotResult(
            chat_id=chat_id,
            reply_text='Я пока понимаю только текстовые сообщения и кнопки.',
            reply_markup=MAIN_MENU_KEYBOARD,
        )

    return process_telegram_text_result(user, text, chat_id)


def handle_telegram_update_once(update):
    update_id = _parse_update_id(update)
    if update_id is None:
        return handle_telegram_update(update)

    if db.session.get(TelegramProcessedUpdate, update_id):
        return TelegramBotResult(chat_id=_update_chat_id(update), reply_text=None)

    marker = TelegramProcessedUpdate(update_id=update_id)
    db.session.add(marker)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return TelegramBotResult(chat_id=_update_chat_id(update), reply_text=None)

    result = handle_telegram_update(update)
    if not db.session.get(TelegramProcessedUpdate, update_id):
        db.session.add(TelegramProcessedUpdate(update_id=update_id))
    _cleanup_processed_updates()
    _cleanup_expired_conversation_states()
    db.session.commit()
    return result


def process_telegram_text(user, text):
    return process_telegram_text_result(user, text, chat_id=None).reply_text


def process_telegram_text_result(user, text, chat_id=None):
    try:
        chat_id = chat_id or user.telegram_id
        text = str(text or '').strip()
        command, tokens = _parse_command(text)
        state = _get_active_state(user)
        explicit_command = _has_explicit_command(text)

        if command == 'cancel' or _word_key(text) in CANCEL_WORDS:
            _clear_state(user)
            return _result(chat_id, 'Отменил текущий ввод.', MAIN_MENU_KEYBOARD)

        if state and not explicit_command:
            return _process_state_message(user, chat_id, state, text)

        if command in FLOW_COMMANDS and not tokens:
            _clear_state(user)
            return _begin_flow(user, chat_id, command)

        if command in {'help', 'privacy', 'debts', 'summary'} or (command in FLOW_COMMANDS and tokens):
            if state:
                _clear_state(user)
            return _process_stateless_command(user, chat_id, command, tokens)

        return _result(chat_id, 'Не понял команду. Нажмите кнопку ниже или напишите /help.', MAIN_MENU_KEYBOARD)
    except ValueError as exc:
        db.session.rollback()
        return _result(chat_id or user.telegram_id, f'Не записал: {exc}', MAIN_MENU_KEYBOARD)
    except Exception as exc:  # pragma: no cover - defensive logging for production webhooks
        db.session.rollback()
        try:
            current_app.logger.exception('Telegram bot command failed: %s', exc)
        except RuntimeError:
            pass
        return _result(chat_id or user.telegram_id, 'Не получилось сохранить запись. Попробуйте позже.', MAIN_MENU_KEYBOARD)


def process_telegram_callback_result(user, chat_id, callback_data):
    try:
        if callback_data == 'tg:cancel':
            _clear_state(user)
            return _result(chat_id, 'Отменил текущий ввод.', MAIN_MENU_KEYBOARD, callback_answer_text='Отменено')

        state = _get_active_state(user)
        if not state:
            return _result(
                chat_id,
                'Этот ввод уже устарел. Выберите действие заново.',
                MAIN_MENU_KEYBOARD,
                callback_answer_text='Устарело',
            )

        if callback_data == 'tg:confirm':
            if state.step != 'confirm':
                return _result(chat_id, 'Сначала заполните все поля.', _cancel_keyboard())
            return _finish_conversation(user, chat_id, state, callback_answer_text='Сохранено')

        if callback_data == 'tg:skip':
            return _process_skip_callback(user, chat_id, state)

        if callback_data.startswith('tg:date:'):
            return _process_date_callback(user, chat_id, state, callback_data.rsplit(':', 1)[-1])

        if callback_data.startswith('tg:expense_category:') and state.flow == 'expense' and state.step == 'category':
            return _set_expense_category(user, chat_id, state, callback_data.rsplit(':', 1)[-1])

        if callback_data.startswith('tg:income_category:') and state.flow == 'income' and state.step == 'category':
            return _set_income_category(user, chat_id, state, callback_data.rsplit(':', 1)[-1])

        if callback_data.startswith('tg:debt_type:') and state.flow == 'debt' and state.step == 'debt_type':
            return _set_debt_type(user, chat_id, state, callback_data.rsplit(':', 1)[-1])

        if callback_data.startswith('tg:payment_debt:') and state.flow == 'payment' and state.step == 'debt':
            debt_id = callback_data.rsplit(':', 1)[-1]
            return _set_payment_debt(user, chat_id, state, debt_id)

        return _result(chat_id, 'Эта кнопка уже не подходит к текущему шагу.', _cancel_keyboard())
    except ValueError as exc:
        db.session.rollback()
        return _result(chat_id, f'Не записал: {exc}', MAIN_MENU_KEYBOARD, callback_answer_text='Ошибка')
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        try:
            current_app.logger.exception('Telegram bot callback failed: %s', exc)
        except RuntimeError:
            pass
        return _result(chat_id, 'Не получилось обработать кнопку. Попробуйте позже.', MAIN_MENU_KEYBOARD)


def send_telegram_message(chat_id, text, bot_token=None, reply_markup=None):
    token = bot_token
    if token is None:
        token = current_app.config.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        return False

    payload = {'chat_id': chat_id, 'text': text}
    if reply_markup is not None:
        payload['reply_markup'] = reply_markup

    response = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    return bool(response.json().get('ok'))


def answer_telegram_callback_query(callback_query_id, text=None, bot_token=None):
    if not callback_query_id:
        return False
    token = bot_token
    if token is None:
        token = current_app.config.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        return False

    payload = {'callback_query_id': callback_query_id}
    if text:
        payload['text'] = text[:200]
    response = requests.post(
        f'https://api.telegram.org/bot{token}/answerCallbackQuery',
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    return bool(response.json().get('ok'))


def send_debt_reminders(days=None, dry_run=False, sender=send_telegram_message):
    if days is None:
        days = current_app.config.get('TELEGRAM_REMINDER_DAYS', 7)
    days = int(days)
    today = date.today()
    stats = {'users_checked': 0, 'messages': 0, 'errors': 0}

    users = User.query.filter(User.telegram_id > 0, User.is_blocked.is_(False)).all()
    for user in users:
        stats['users_checked'] += 1
        message = build_debt_reminder_message(user, today=today, days=days)
        if not message:
            continue
        stats['messages'] += 1
        if dry_run:
            continue
        try:
            sender(user.telegram_id, message, reply_markup=MAIN_MENU_KEYBOARD)
        except Exception as exc:  # pragma: no cover - depends on Telegram network availability
            stats['errors'] += 1
            current_app.logger.warning('Telegram reminder failed for user %s: %s', user.id, exc)
    return stats


def build_debt_reminder_message(user, today=None, days=7):
    today = today or date.today()
    deadline = today + timedelta(days=days)
    debts = Debt.query.filter_by(user_id=user.id, status='active').all()
    due_debts = []
    for debt in debts:
        due_date = _effective_due_date(debt, today)
        if due_date and due_date <= deadline:
            due_debts.append((due_date, debt))

    if not due_debts:
        return None

    lines = ['Напоминание по долгам:']
    for due_date, debt in sorted(due_debts, key=lambda item: item[0]):
        diff = (due_date - today).days
        if diff < 0:
            timing = f'просрочен на {abs(diff)} дн.'
        elif diff == 0:
            timing = 'сегодня'
        else:
            timing = f'через {diff} дн.'
        amount = debt.effective_next_payment_amount() or debt.remaining_amount
        lines.append(
            f'{debt.bank_name} {debt.product_name}: {timing}, платеж {_format_money(amount)}, остаток {_format_money(debt.remaining_amount)}'
        )
    return '\n'.join(lines)


def _process_stateless_command(user, chat_id, command, tokens):
    if command == 'help':
        return _result(chat_id, HELP_TEXT, MAIN_MENU_KEYBOARD)
    if command == 'privacy':
        return _result(chat_id, PRIVACY_TEXT, MAIN_MENU_KEYBOARD)
    if command == 'expense':
        return _result(chat_id, _create_expense(user, tokens), MAIN_MENU_KEYBOARD)
    if command == 'income':
        return _result(chat_id, _create_income(user, tokens), MAIN_MENU_KEYBOARD)
    if command == 'debt':
        return _result(chat_id, _create_debt(user, tokens), MAIN_MENU_KEYBOARD)
    if command == 'payment':
        return _result(chat_id, _create_payment(user, tokens), MAIN_MENU_KEYBOARD)
    if command == 'debts':
        return _result(chat_id, _list_debts(user), MAIN_MENU_KEYBOARD)
    if command == 'summary':
        return _result(chat_id, _build_summary(user), MAIN_MENU_KEYBOARD)
    return _result(chat_id, 'Не понял команду. Нажмите кнопку ниже или напишите /help.', MAIN_MENU_KEYBOARD)


def _begin_flow(user, chat_id, flow):
    if flow == 'expense':
        _save_state(user, chat_id, flow='expense', step='amount', data={})
        return _result(chat_id, 'Введите сумму расхода. Например: 850', _cancel_keyboard())
    if flow == 'income':
        _save_state(user, chat_id, flow='income', step='amount', data={})
        return _result(chat_id, 'Введите сумму дохода. Например: 120000', _cancel_keyboard())
    if flow == 'debt':
        _save_state(user, chat_id, flow='debt', step='amount', data={})
        return _result(chat_id, 'Введите сумму долга. Например: 300000', _cancel_keyboard())
    if flow == 'payment':
        debts = Debt.query.filter_by(user_id=user.id, status='active').order_by(Debt.id.asc()).all()
        if not debts:
            return _result(chat_id, 'Активных долгов нет. Сначала добавьте долг.', MAIN_MENU_KEYBOARD)
        _save_state(user, chat_id, flow='payment', step='debt', data={})
        if len(debts) == 1:
            return _set_payment_debt(user, chat_id, _get_active_state(user), debts[0].id)
        return _result(chat_id, 'Выберите долг для платежа:', _debt_keyboard(debts))
    return _result(chat_id, 'Не понял действие.', MAIN_MENU_KEYBOARD)


def _process_state_message(user, chat_id, state, text):
    if state.step == 'confirm':
        key = _word_key(text)
        if key in CONFIRM_WORDS:
            return _finish_conversation(user, chat_id, state)
        if key in REJECT_WORDS:
            _clear_state(user)
            return _result(chat_id, 'Отменил сохранение.', MAIN_MENU_KEYBOARD)
        return _result(chat_id, 'Нажмите "Сохранить" или "Отмена".', _confirm_keyboard())

    if state.flow == 'expense':
        return _process_expense_message(user, chat_id, state, text)
    if state.flow == 'income':
        return _process_income_message(user, chat_id, state, text)
    if state.flow == 'debt':
        return _process_debt_message(user, chat_id, state, text)
    if state.flow == 'payment':
        return _process_payment_message(user, chat_id, state, text)

    _clear_state(user)
    return _result(chat_id, 'Сценарий устарел. Выберите действие заново.', MAIN_MENU_KEYBOARD)


def _process_expense_message(user, chat_id, state, text):
    data = _state_data(state)
    if state.step == 'amount':
        amount = _parse_money(text)
        if amount <= 0:
            raise ValueError('Сумма расхода должна быть больше нуля.')
        data['amount'] = str(amount)
        _save_state(user, chat_id, flow='expense', step='category', data=data, state=state)
        return _result(chat_id, 'Выберите категорию расхода:', _choice_keyboard('expense_category', EXPENSE_LABELS))

    if state.step == 'category':
        category = EXPENSE_ALIASES.get(_word_key(text))
        if not category:
            return _result(chat_id, 'Не понял категорию. Выберите кнопку или напишите название категории.', _choice_keyboard('expense_category', EXPENSE_LABELS))
        return _set_expense_category(user, chat_id, state, category)

    if state.step == 'title':
        data['title'] = str(text).strip()[:150] or EXPENSE_LABELS.get(data.get('category'), 'Расход')
        _save_state(user, chat_id, flow='expense', step='date', data=data, state=state)
        return _result(chat_id, 'Когда был расход? Можно написать дату или выбрать кнопку.', _date_keyboard(include_skip=False))

    if state.step == 'date':
        data['expense_date'] = _parse_date_value(text).isoformat()
        _save_state(user, chat_id, flow='expense', step='confirm', data=data, state=state)
        return _expense_confirm_result(chat_id, data)

    return _result(chat_id, 'Сценарий расхода устарел. Начните заново.', MAIN_MENU_KEYBOARD)


def _process_income_message(user, chat_id, state, text):
    data = _state_data(state)
    if state.step == 'amount':
        amount = _parse_money(text)
        if amount <= 0:
            raise ValueError('Сумма дохода должна быть больше нуля.')
        data['amount'] = str(amount)
        _save_state(user, chat_id, flow='income', step='category', data=data, state=state)
        return _result(chat_id, 'Выберите категорию дохода:', _choice_keyboard('income_category', INCOME_LABELS))

    if state.step == 'category':
        category = INCOME_ALIASES.get(_word_key(text))
        if not category:
            return _result(chat_id, 'Не понял категорию. Выберите кнопку или напишите название категории.', _choice_keyboard('income_category', INCOME_LABELS))
        return _set_income_category(user, chat_id, state, category)

    if state.step == 'source':
        data['source'] = str(text).strip()[:150] or INCOME_LABELS.get(data.get('category'), 'Доход')
        _save_state(user, chat_id, flow='income', step='date', data=data, state=state)
        return _result(chat_id, 'Когда был доход? Можно написать дату или выбрать кнопку.', _date_keyboard(include_skip=False))

    if state.step == 'date':
        data['income_date'] = _parse_date_value(text).isoformat()
        _save_state(user, chat_id, flow='income', step='confirm', data=data, state=state)
        return _income_confirm_result(chat_id, data)

    return _result(chat_id, 'Сценарий дохода устарел. Начните заново.', MAIN_MENU_KEYBOARD)


def _process_debt_message(user, chat_id, state, text):
    data = _state_data(state)
    if state.step == 'amount':
        amount = _parse_money(text)
        if amount <= 0:
            raise ValueError('Сумма долга должна быть больше нуля.')
        data['amount'] = str(amount)
        _save_state(user, chat_id, flow='debt', step='bank_name', data=data, state=state)
        return _result(chat_id, 'Введите банк или кредитора.', _cancel_keyboard())

    if state.step == 'bank_name':
        bank_name = str(text).strip()
        if not bank_name:
            raise ValueError('Банк или кредитор обязательны.')
        data['bank_name'] = bank_name[:100]
        _save_state(user, chat_id, flow='debt', step='debt_type', data=data, state=state)
        return _result(chat_id, 'Выберите тип долга:', _choice_keyboard('debt_type', DEBT_TYPE_LABELS))

    if state.step == 'debt_type':
        debt_type = DEBT_TYPE_ALIASES.get(_word_key(text))
        if not debt_type:
            return _result(chat_id, 'Не понял тип долга. Выберите кнопку.', _choice_keyboard('debt_type', DEBT_TYPE_LABELS))
        return _set_debt_type(user, chat_id, state, debt_type)

    if state.step == 'product_name':
        data['product_name'] = str(text).strip()[:150] or DEBT_TYPE_LABELS.get(data.get('debt_type'), 'Долг')
        _save_state(user, chat_id, flow='debt', step='minimum_payment', data=data, state=state)
        return _result(chat_id, 'Введите минимальный платеж или нажмите "Пропустить".', _skip_keyboard())

    if state.step == 'minimum_payment':
        amount = _parse_money(text)
        data['minimum_payment'] = str(amount)
        _save_state(user, chat_id, flow='debt', step='interest_rate', data=data, state=state)
        return _result(chat_id, 'Введите процентную ставку или нажмите "Пропустить".', _skip_keyboard())

    if state.step == 'interest_rate':
        rate = _parse_money(text)
        data['interest_rate'] = str(rate)
        _save_state(user, chat_id, flow='debt', step='next_payment_date', data=data, state=state)
        return _result(chat_id, 'Введите дату следующего платежа или нажмите "Пропустить".', _date_keyboard(include_skip=True))

    if state.step == 'next_payment_date':
        data['next_payment_date'] = _parse_date_value(text).isoformat()
        _save_state(user, chat_id, flow='debt', step='confirm', data=data, state=state)
        return _debt_confirm_result(chat_id, data)

    return _result(chat_id, 'Сценарий долга устарел. Начните заново.', MAIN_MENU_KEYBOARD)


def _process_payment_message(user, chat_id, state, text):
    data = _state_data(state)
    if state.step == 'debt':
        debt = _find_debt(user, _split_tokens(text))
        data['debt_id'] = debt.id
        _save_state(user, chat_id, flow='payment', step='amount', data=data, state=state)
        return _result(chat_id, f'Долг: #{debt.id} {debt.bank_name} {debt.product_name}.\nВведите сумму платежа.', _cancel_keyboard())

    if state.step == 'amount':
        amount = _parse_money(text)
        if amount <= 0:
            raise ValueError('Сумма платежа должна быть больше нуля.')
        data['amount'] = str(amount)
        _save_state(user, chat_id, flow='payment', step='date', data=data, state=state)
        return _result(chat_id, 'Когда был платеж? Можно написать дату или выбрать кнопку.', _date_keyboard(include_skip=False))

    if state.step == 'date':
        data['payment_date'] = _parse_date_value(text).isoformat()
        _save_state(user, chat_id, flow='payment', step='confirm', data=data, state=state)
        return _payment_confirm_result(user, chat_id, data)

    return _result(chat_id, 'Сценарий платежа устарел. Начните заново.', MAIN_MENU_KEYBOARD)


def _process_skip_callback(user, chat_id, state):
    data = _state_data(state)
    if state.flow == 'expense' and state.step == 'title':
        data['title'] = EXPENSE_LABELS.get(data.get('category'), 'Расход')
        _save_state(user, chat_id, flow='expense', step='date', data=data, state=state)
        return _result(chat_id, 'Когда был расход? Можно написать дату или выбрать кнопку.', _date_keyboard(include_skip=False), callback_answer_text='Пропущено')

    if state.flow == 'income' and state.step == 'source':
        data['source'] = INCOME_LABELS.get(data.get('category'), 'Доход')
        _save_state(user, chat_id, flow='income', step='date', data=data, state=state)
        return _result(chat_id, 'Когда был доход? Можно написать дату или выбрать кнопку.', _date_keyboard(include_skip=False), callback_answer_text='Пропущено')

    if state.flow == 'debt' and state.step == 'product_name':
        data['product_name'] = DEBT_TYPE_LABELS.get(data.get('debt_type'), 'Долг')
        _save_state(user, chat_id, flow='debt', step='minimum_payment', data=data, state=state)
        return _result(chat_id, 'Введите минимальный платеж или нажмите "Пропустить".', _skip_keyboard(), callback_answer_text='Пропущено')

    if state.flow == 'debt' and state.step == 'minimum_payment':
        data['minimum_payment'] = None
        _save_state(user, chat_id, flow='debt', step='interest_rate', data=data, state=state)
        return _result(chat_id, 'Введите процентную ставку или нажмите "Пропустить".', _skip_keyboard(), callback_answer_text='Пропущено')

    if state.flow == 'debt' and state.step == 'interest_rate':
        data['interest_rate'] = None
        _save_state(user, chat_id, flow='debt', step='next_payment_date', data=data, state=state)
        return _result(chat_id, 'Введите дату следующего платежа или нажмите "Пропустить".', _date_keyboard(include_skip=True), callback_answer_text='Пропущено')

    if state.flow == 'debt' and state.step == 'next_payment_date':
        data['next_payment_date'] = None
        _save_state(user, chat_id, flow='debt', step='confirm', data=data, state=state)
        result = _debt_confirm_result(chat_id, data)
        result.callback_answer_text = 'Пропущено'
        return result

    return _result(chat_id, 'На этом шаге нельзя пропустить поле.', _cancel_keyboard())


def _process_date_callback(user, chat_id, state, value):
    data = _state_data(state)
    if state.step not in {'date', 'next_payment_date'}:
        return _result(chat_id, 'Дата сейчас не ожидается.', _cancel_keyboard())

    if value == 'today':
        value_date = date.today()
    elif value == 'yesterday':
        value_date = date.today() - timedelta(days=1)
    else:
        raise ValueError('Не понял дату.')

    if state.flow == 'expense':
        data['expense_date'] = value_date.isoformat()
        _save_state(user, chat_id, flow='expense', step='confirm', data=data, state=state)
        result = _expense_confirm_result(chat_id, data)
    elif state.flow == 'income':
        data['income_date'] = value_date.isoformat()
        _save_state(user, chat_id, flow='income', step='confirm', data=data, state=state)
        result = _income_confirm_result(chat_id, data)
    elif state.flow == 'payment':
        data['payment_date'] = value_date.isoformat()
        _save_state(user, chat_id, flow='payment', step='confirm', data=data, state=state)
        result = _payment_confirm_result(user, chat_id, data)
    elif state.flow == 'debt':
        data['next_payment_date'] = value_date.isoformat()
        _save_state(user, chat_id, flow='debt', step='confirm', data=data, state=state)
        result = _debt_confirm_result(chat_id, data)
    else:
        raise ValueError('Сценарий устарел.')

    result.callback_answer_text = 'Дата выбрана'
    return result


def _set_expense_category(user, chat_id, state, category):
    if category not in EXPENSE_LABELS:
        raise ValueError('Некорректная категория расхода.')
    data = _state_data(state)
    data['category'] = category
    _save_state(user, chat_id, flow='expense', step='title', data=data, state=state)
    return _result(chat_id, 'Что купили или за что заплатили?', _skip_keyboard(), callback_answer_text='Категория выбрана')


def _set_income_category(user, chat_id, state, category):
    if category not in INCOME_LABELS:
        raise ValueError('Некорректная категория дохода.')
    data = _state_data(state)
    data['category'] = category
    _save_state(user, chat_id, flow='income', step='source', data=data, state=state)
    return _result(chat_id, 'Укажите источник дохода.', _skip_keyboard(), callback_answer_text='Категория выбрана')


def _set_debt_type(user, chat_id, state, debt_type):
    if debt_type not in DEBT_TYPE_LABELS:
        raise ValueError('Некорректный тип долга.')
    data = _state_data(state)
    data['debt_type'] = debt_type
    _save_state(user, chat_id, flow='debt', step='product_name', data=data, state=state)
    return _result(chat_id, 'Введите название продукта или нажмите "Пропустить".', _skip_keyboard(), callback_answer_text='Тип выбран')


def _set_payment_debt(user, chat_id, state, debt_id):
    debt = Debt.query.filter_by(id=int(debt_id), user_id=user.id, status='active').first()
    if not debt:
        raise ValueError('Долг не найден.')
    data = _state_data(state)
    data['debt_id'] = debt.id
    _save_state(user, chat_id, flow='payment', step='amount', data=data, state=state)
    return _result(
        chat_id,
        f'Долг: #{debt.id} {debt.bank_name} {debt.product_name}.\nВведите сумму платежа.',
        _cancel_keyboard(),
        callback_answer_text='Долг выбран',
    )


def _finish_conversation(user, chat_id, state, callback_answer_text=None):
    data = _state_data(state)
    if state.flow == 'expense':
        expense = Expense(
            user_id=user.id,
            amount=_parse_money(data['amount']),
            category=data['category'],
            title=(data.get('title') or EXPENSE_LABELS.get(data['category'], 'Расход'))[:150],
            expense_date=_parse_date_value(data['expense_date']),
            payment_method='card',
            comment='Telegram',
        )
        db.session.add(expense)
        _clear_state(user)
        db.session.commit()
        return _result(chat_id, f'Записал расход: {_format_money(expense.amount)} — {expense.title}.', MAIN_MENU_KEYBOARD, callback_answer_text)

    if state.flow == 'income':
        income = Income(
            user_id=user.id,
            amount=_parse_money(data['amount']),
            category=data['category'],
            source=(data.get('source') or INCOME_LABELS.get(data['category'], 'Доход'))[:150],
            income_date=_parse_date_value(data['income_date']),
            comment='Telegram',
        )
        db.session.add(income)
        _clear_state(user)
        db.session.commit()
        return _result(chat_id, f'Записал доход: {_format_money(income.amount)} — {income.source}.', MAIN_MENU_KEYBOARD, callback_answer_text)

    if state.flow == 'debt':
        next_payment_date = _parse_optional_date(data.get('next_payment_date'))
        debt = Debt(
            user_id=user.id,
            bank_name=data['bank_name'][:100],
            debt_type=data['debt_type'],
            product_name=(data.get('product_name') or DEBT_TYPE_LABELS.get(data['debt_type'], 'Долг'))[:150],
            total_amount=_parse_money(data['amount']),
            remaining_amount=_parse_money(data['amount']),
            minimum_payment=_parse_optional_money(data.get('minimum_payment'), 'Минимальный платеж'),
            interest_rate=_parse_optional_money(data.get('interest_rate'), 'Ставка'),
            next_payment_date=next_payment_date,
            is_payment_recurring=bool(next_payment_date),
            status='active',
            comment='Telegram',
        )
        db.session.add(debt)
        _clear_state(user)
        db.session.commit()
        return _result(chat_id, f'Добавил долг #{debt.id}: {debt.bank_name} {debt.product_name} на {_format_money(debt.remaining_amount)}.', MAIN_MENU_KEYBOARD, callback_answer_text)

    if state.flow == 'payment':
        debt = Debt.query.filter_by(id=int(data['debt_id']), user_id=user.id, status='active').first()
        if not debt:
            raise ValueError('Долг не найден.')
        payment = add_payment(
            debt,
            _parse_money(data['amount']),
            payment_date=_parse_date_value(data['payment_date']),
            comment='Telegram',
        )
        _clear_state(user)
        db.session.commit()
        return _result(
            chat_id,
            f'Записал платеж: {_format_money(payment.amount)} по долгу #{debt.id} {debt.bank_name} {debt.product_name}.\nОстаток: {_format_money(debt.remaining_amount)}.',
            MAIN_MENU_KEYBOARD,
            callback_answer_text,
        )

    _clear_state(user)
    return _result(chat_id, 'Сценарий устарел. Выберите действие заново.', MAIN_MENU_KEYBOARD)


def _expense_confirm_result(chat_id, data):
    return _result(
        chat_id,
        'Проверьте расход:\n'
        f'Сумма: {_format_money(data.get("amount"))}\n'
        f'Категория: {EXPENSE_LABELS.get(data.get("category"), data.get("category"))}\n'
        f'Название: {data.get("title") or EXPENSE_LABELS.get(data.get("category"), "Расход")}\n'
        f'Дата: {_format_date(_parse_date_value(data.get("expense_date")))}',
        _confirm_keyboard(),
    )


def _income_confirm_result(chat_id, data):
    return _result(
        chat_id,
        'Проверьте доход:\n'
        f'Сумма: {_format_money(data.get("amount"))}\n'
        f'Категория: {INCOME_LABELS.get(data.get("category"), data.get("category"))}\n'
        f'Источник: {data.get("source") or INCOME_LABELS.get(data.get("category"), "Доход")}\n'
        f'Дата: {_format_date(_parse_date_value(data.get("income_date")))}',
        _confirm_keyboard(),
    )


def _debt_confirm_result(chat_id, data):
    next_date = _parse_optional_date(data.get('next_payment_date'))
    return _result(
        chat_id,
        'Проверьте долг:\n'
        f'Сумма: {_format_money(data.get("amount"))}\n'
        f'Банк: {data.get("bank_name")}\n'
        f'Тип: {DEBT_TYPE_LABELS.get(data.get("debt_type"), data.get("debt_type"))}\n'
        f'Продукт: {data.get("product_name") or DEBT_TYPE_LABELS.get(data.get("debt_type"), "Долг")}\n'
        f'Минимальный платеж: {_format_money(data.get("minimum_payment")) if data.get("minimum_payment") else "не указан"}\n'
        f'Ставка: {data.get("interest_rate") if data.get("interest_rate") else "не указана"}\n'
        f'Следующий платеж: {_format_date(next_date)}',
        _confirm_keyboard(),
    )


def _payment_confirm_result(user, chat_id, data):
    debt = Debt.query.filter_by(id=int(data['debt_id']), user_id=user.id).first()
    debt_label = f'#{debt.id} {debt.bank_name} {debt.product_name}' if debt else f'#{data["debt_id"]}'
    return _result(
        chat_id,
        'Проверьте платеж:\n'
        f'Долг: {debt_label}\n'
        f'Сумма: {_format_money(data.get("amount"))}\n'
        f'Дата: {_format_date(_parse_date_value(data.get("payment_date")))}',
        _confirm_keyboard(),
    )


def _create_expense(user, raw_tokens):
    tokens, options = _extract_options(raw_tokens)
    amount, tokens = _pull_amount(tokens, 'Укажите сумму расхода.')
    if amount <= 0:
        raise ValueError('Сумма расхода должна быть больше нуля.')

    expense_date, tokens = _pull_date(tokens, options)
    payment_method, tokens = _pull_choice(tokens, options.get('payment_method'), PAYMENT_METHOD_ALIASES)
    category, tokens, matched = _pull_choice(tokens, options.get('category'), EXPENSE_ALIASES, return_matched=True)
    category = category or 'other'
    payment_method = payment_method or 'card'
    title = _join_tokens(tokens) or matched or EXPENSE_LABELS.get(category) or 'Расход из Telegram'

    expense = Expense(
        user_id=user.id,
        amount=amount,
        category=category,
        title=title[:150],
        expense_date=expense_date,
        payment_method=payment_method,
        comment='Telegram',
    )
    db.session.add(expense)
    db.session.commit()

    return (
        f'Записал расход: {_format_money(expense.amount)} — {expense.title}.\n'
        f'Категория: {EXPENSE_LABELS.get(expense.category, expense.category)}, дата: {_format_date(expense.expense_date)}.'
    )


def _create_income(user, raw_tokens):
    tokens, options = _extract_options(raw_tokens)
    amount, tokens = _pull_amount(tokens, 'Укажите сумму дохода.')
    if amount <= 0:
        raise ValueError('Сумма дохода должна быть больше нуля.')

    income_date, tokens = _pull_date(tokens, options)
    category, tokens, matched = _pull_choice(tokens, options.get('category'), INCOME_ALIASES, return_matched=True)
    category = category or 'other'
    source = _join_tokens(tokens) or matched or INCOME_LABELS.get(category) or 'Telegram'

    income = Income(
        user_id=user.id,
        amount=amount,
        category=category,
        source=source[:150],
        income_date=income_date,
        comment='Telegram',
    )
    db.session.add(income)
    db.session.commit()

    return (
        f'Записал доход: {_format_money(income.amount)} — {income.source}.\n'
        f'Категория: {INCOME_LABELS.get(income.category, income.category)}, дата: {_format_date(income.income_date)}.'
    )


def _create_debt(user, raw_tokens):
    tokens, options = _extract_options(raw_tokens)
    amount, tokens = _pull_amount(tokens, 'Укажите сумму долга.')
    if amount <= 0:
        raise ValueError('Сумма долга должна быть больше нуля.')

    next_payment_date, tokens = _pull_date(tokens, options, required=False, default_today=False)
    debt_type, tokens, matched_type = _pull_choice(tokens, options.get('debt_type'), DEBT_TYPE_ALIASES, return_matched=True)
    debt_type = debt_type or 'consumer_credit'
    minimum_payment = _parse_optional_money(options.get('minimum_payment'), 'Минимальный платеж')
    interest_rate = _parse_optional_money(options.get('interest_rate'), 'Ставка')

    bank_name = str(options.get('bank') or '').strip()
    product_name = str(options.get('product') or '').strip()
    if not bank_name:
        if not tokens:
            raise ValueError('Укажите банк или название долга.')
        bank_name = tokens.pop(0)
    if not product_name:
        product_name = _join_tokens(tokens) or matched_type or DEBT_TYPE_LABELS.get(debt_type, 'Долг')

    debt = Debt(
        user_id=user.id,
        bank_name=bank_name[:100],
        debt_type=debt_type,
        product_name=product_name[:150],
        total_amount=amount,
        remaining_amount=amount,
        minimum_payment=minimum_payment,
        interest_rate=interest_rate,
        next_payment_date=next_payment_date,
        is_payment_recurring=bool(next_payment_date),
        status='active',
        comment='Telegram',
    )
    db.session.add(debt)
    db.session.commit()

    date_part = f'\nСледующий платеж: {_format_date(debt.next_payment_date)}.' if debt.next_payment_date else ''
    return (
        f'Добавил долг #{debt.id}: {debt.bank_name} {debt.product_name} на {_format_money(debt.remaining_amount)}.'
        f'{date_part}'
    )


def _create_payment(user, raw_tokens):
    tokens, options = _extract_options(raw_tokens)
    amount, tokens = _pull_amount(tokens, 'Укажите сумму платежа.')
    if amount <= 0:
        raise ValueError('Сумма платежа должна быть больше нуля.')

    payment_date, tokens = _pull_date(tokens, options)
    is_early_repayment, tokens = _pull_early_repayment(tokens, options)
    tokens = [token for token in tokens if _word_key(token) not in {'долг', 'кредит', 'за', 'по'}]
    debt = _find_debt(user, tokens, options.get('debt_id'))
    if debt.status != 'active':
        raise ValueError('Нельзя внести платеж в архивный долг.')

    comment = 'Telegram'
    query_text = _join_tokens(tokens)
    if query_text:
        comment = f'Telegram: {query_text}'

    payment = add_payment(
        debt,
        amount,
        payment_date=payment_date,
        comment=comment,
        is_early_repayment=is_early_repayment,
    )
    advanced = bool(getattr(payment, 'next_payment_date_advanced', False))
    advanced_text = '\nДата следующего платежа обновлена.' if advanced else ''

    return (
        f'Записал платеж: {_format_money(payment.amount)} по долгу #{debt.id} '
        f'{debt.bank_name} {debt.product_name}.\n'
        f'Остаток: {_format_money(debt.remaining_amount)}.{advanced_text}'
    )


def _list_debts(user):
    today = date.today()
    debts = Debt.query.filter_by(user_id=user.id, status='active').all()
    debts = sorted(debts, key=lambda debt: _effective_due_date(debt, today) or date.max)
    if not debts:
        return 'Активных долгов нет.'

    lines = ['Активные долги:']
    for index, debt in enumerate(debts[:10], start=1):
        due_date = _effective_due_date(debt, today)
        due_part = f', платеж {_format_date(due_date)}' if due_date else ''
        lines.append(
            f'{index}. #{debt.id} {debt.bank_name} {debt.product_name}: '
            f'остаток {_format_money(debt.remaining_amount)}{due_part}'
        )
    if len(debts) > 10:
        lines.append(f'И еще {len(debts) - 10}...')
    return '\n'.join(lines)


def _build_summary(user):
    summary = get_finance_summary(user.id)
    month_start = summary.get('month_start') or date.today().replace(day=1)
    lines = [
        f'Итог за {month_start.strftime("%m.%Y")}:',
        f'Доходы: {_format_money(summary.get("total_incomes", 0))}',
        f'Расходы: {_format_money(summary.get("total_expenses", 0))}',
        f'Платежи по долгам: {_format_money(summary.get("total_payments", 0))}',
        f'Свободный остаток: {_format_money(summary.get("free_balance", 0))}',
        f'Остаток по долгам: {_format_money(summary.get("total_remaining", 0))}',
    ]

    nearest = summary.get('nearest_debt')
    if nearest:
        due_date = _effective_due_date(nearest, date.today())
        lines.append(f'Ближайший платеж: {nearest.bank_name} {nearest.product_name}, {_format_date(due_date)}.')
    return '\n'.join(lines)


def _find_debt(user, tokens, debt_id=None):
    if debt_id:
        try:
            parsed_id = int(str(debt_id).strip().lstrip('#'))
        except (TypeError, ValueError):
            raise ValueError('Некорректный номер долга.')
        debt = Debt.query.filter_by(id=parsed_id, user_id=user.id).first()
        if not debt:
            raise ValueError(f'Долг #{parsed_id} не найден.')
        return debt

    debts = Debt.query.filter_by(user_id=user.id, status='active').all()
    if not debts:
        raise ValueError('У вас нет активных долгов.')
    if not tokens:
        if len(debts) == 1:
            return debts[0]
        raise ValueError('Укажите банк, продукт или id долга. Например: платеж 5000 сбер.')

    query = _normalize_text(_join_tokens(tokens))
    scored = []
    query_tokens = {item for item in query.split() if item}
    for debt in debts:
        haystack = _normalize_text(f'{debt.id} {debt.bank_name} {debt.product_name} {debt.comment or ""}')
        score = 0
        if query and query in haystack:
            score += 100
        score += sum(20 for token in query_tokens if token in haystack)
        if str(debt.id) in query_tokens:
            score += 200
        if score:
            scored.append((score, debt))

    if not scored:
        raise ValueError('Не нашел подходящий долг. Нажмите "Долги", чтобы посмотреть список.')

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score = scored[0][0]
    best = [debt for score, debt in scored if score == best_score]
    if len(best) > 1:
        variants = ', '.join(f'#{debt.id} {debt.bank_name} {debt.product_name}' for debt in best[:5])
        raise ValueError(f'Нашлось несколько долгов: {variants}. Укажите id, например долг=#{best[0].id}.')
    return best[0]


def _state_data(state):
    try:
        data = json.loads(state.data or '{}')
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_state(user, chat_id, flow, step, data, state=None):
    state = state or TelegramConversationState.query.filter_by(telegram_id=user.telegram_id).first()
    if not state:
        state = TelegramConversationState(telegram_id=user.telegram_id, chat_id=chat_id, flow=flow, step=step)
        db.session.add(state)
    state.chat_id = chat_id
    state.flow = flow
    state.step = step
    state.data = json.dumps(data, ensure_ascii=False, sort_keys=True)
    state.expires_at = datetime.utcnow() + timedelta(minutes=_conversation_ttl_minutes())
    state.updated_at = datetime.utcnow()
    db.session.flush()
    return state


def _get_active_state(user):
    _cleanup_expired_conversation_states()
    state = TelegramConversationState.query.filter_by(telegram_id=user.telegram_id).first()
    if not state:
        return None
    if state.expires_at <= datetime.utcnow():
        db.session.delete(state)
        db.session.flush()
        return None
    return state


def _clear_state(user):
    state = TelegramConversationState.query.filter_by(telegram_id=user.telegram_id).first()
    if state:
        db.session.delete(state)
        db.session.flush()


def _conversation_ttl_minutes():
    try:
        ttl = int(current_app.config.get('TELEGRAM_CONVERSATION_TTL_MINUTES', 30))
    except (TypeError, ValueError):
        ttl = 30
    return max(ttl, 1)


def _cleanup_expired_conversation_states():
    threshold = datetime.utcnow()
    TelegramConversationState.query.filter(TelegramConversationState.expires_at <= threshold).delete()


def _cleanup_processed_updates():
    try:
        retention_days = int(current_app.config.get('TELEGRAM_UPDATE_RETENTION_DAYS', 30))
    except (TypeError, ValueError):
        retention_days = 30
    if retention_days <= 0:
        return
    threshold = datetime.utcnow() - timedelta(days=retention_days)
    TelegramProcessedUpdate.query.filter(TelegramProcessedUpdate.created_at < threshold).delete()


def _result(chat_id, text, reply_markup=None, callback_answer_text=None):
    return TelegramBotResult(
        chat_id=chat_id,
        reply_text=text,
        reply_markup=reply_markup,
        callback_answer_text=callback_answer_text,
    )


def _cancel_keyboard():
    return {'inline_keyboard': [[{'text': 'Отмена', 'callback_data': 'tg:cancel'}]]}


def _skip_keyboard():
    return {
        'inline_keyboard': [
            [{'text': 'Пропустить', 'callback_data': 'tg:skip'}],
            [{'text': 'Отмена', 'callback_data': 'tg:cancel'}],
        ]
    }


def _confirm_keyboard():
    return {
        'inline_keyboard': [
            [{'text': 'Сохранить', 'callback_data': 'tg:confirm'}],
            [{'text': 'Отмена', 'callback_data': 'tg:cancel'}],
        ]
    }


def _date_keyboard(include_skip=False):
    rows = [
        [
            {'text': 'Сегодня', 'callback_data': 'tg:date:today'},
            {'text': 'Вчера', 'callback_data': 'tg:date:yesterday'},
        ]
    ]
    if include_skip:
        rows.append([{'text': 'Пропустить', 'callback_data': 'tg:skip'}])
    rows.append([{'text': 'Отмена', 'callback_data': 'tg:cancel'}])
    return {'inline_keyboard': rows}


def _choice_keyboard(kind, labels):
    rows = []
    current = []
    for key, label in labels.items():
        current.append({'text': label.title(), 'callback_data': f'tg:{kind}:{key}'})
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append([{'text': 'Отмена', 'callback_data': 'tg:cancel'}])
    return {'inline_keyboard': rows}


def _debt_keyboard(debts):
    rows = []
    for debt in debts[:10]:
        label = f'#{debt.id} {debt.bank_name} {debt.product_name}'
        if len(label) > 45:
            label = label[:42] + '...'
        rows.append([{'text': label, 'callback_data': f'tg:payment_debt:{debt.id}'}])
    rows.append([{'text': 'Отмена', 'callback_data': 'tg:cancel'}])
    return {'inline_keyboard': rows}


def _update_context(update):
    callback = update.get('callback_query') or {}
    if callback:
        message = callback.get('message') or {}
        chat = message.get('chat') or {}
        sender = callback.get('from') or {}
        return {
            'chat_id': chat.get('id'),
            'chat_type': chat.get('type'),
            'telegram_id': sender.get('id'),
            'callback_data': callback.get('data'),
            'callback_query_id': callback.get('id'),
            'text': None,
        }

    message = update.get('message') or update.get('edited_message') or {}
    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    return {
        'chat_id': chat.get('id'),
        'chat_type': chat.get('type'),
        'telegram_id': sender.get('id'),
        'callback_data': None,
        'callback_query_id': None,
        'text': str(message.get('text') or '').strip(),
    }


def _parse_command(text):
    tokens = _split_tokens(text)
    if not tokens:
        return 'help', []

    first = tokens[0]
    if _looks_like_amount(first):
        amount = _parse_money(first)
        if amount < 0:
            return 'expense', [first[1:]] + tokens[1:]
        return 'income', tokens

    command_key = _command_key(first)
    command = COMMAND_ALIASES.get(command_key)
    if command:
        return command, tokens[1:]
    return None, tokens


def _has_explicit_command(text):
    tokens = _split_tokens(text)
    if not tokens:
        return False
    if _looks_like_amount(tokens[0]):
        return False
    return _command_key(tokens[0]) in COMMAND_ALIASES


def _parse_update_id(update):
    try:
        return int(update.get('update_id'))
    except (AttributeError, TypeError, ValueError):
        return None


def _update_chat_id(update):
    message = update.get('message') or update.get('edited_message') or {}
    if not message and update.get('callback_query'):
        message = (update.get('callback_query') or {}).get('message') or {}
    chat = message.get('chat') or {}
    return chat.get('id')


def _pull_amount(tokens, empty_message):
    for index, token in enumerate(tokens):
        if _looks_like_amount(token):
            amount = _parse_money(token)
            return abs(amount), tokens[:index] + tokens[index + 1:]
    raise ValueError(empty_message)


def _pull_date(tokens, options, required=False, default_today=True):
    option_date = options.get('date')
    if option_date:
        return _parse_date_value(option_date), tokens

    for index, token in enumerate(tokens):
        parsed = _try_parse_date_value(token)
        if parsed:
            return parsed, tokens[:index] + tokens[index + 1:]

    if required:
        raise ValueError('Укажите дату.')
    return (date.today() if default_today else None), tokens


def _pull_choice(tokens, option_value, aliases, return_matched=False):
    if option_value:
        key = _word_key(option_value)
        value = aliases.get(key)
        if not value:
            raise ValueError(f'Не понял значение: {option_value}.')
        if return_matched:
            return value, tokens, option_value
        return value, tokens

    for index, token in enumerate(tokens):
        key = _word_key(token)
        value = aliases.get(key)
        if value:
            next_tokens = tokens[:index] + tokens[index + 1:]
            if return_matched:
                return value, next_tokens, token
            return value, next_tokens

    if return_matched:
        return None, tokens, None
    return None, tokens


def _pull_early_repayment(tokens, options):
    value = options.get('early')
    is_early = _is_truthy(value) if value is not None else False
    next_tokens = []
    for token in tokens:
        key = _word_key(token)
        if key in {'досрочно', 'досрочный', 'раньше', 'early'}:
            is_early = True
            continue
        next_tokens.append(token)
    return is_early, next_tokens


def _extract_options(tokens):
    options = {}
    next_tokens = []
    for token in tokens:
        if '=' not in token:
            next_tokens.append(token)
            continue
        key, value = token.split('=', 1)
        key = OPTION_ALIASES.get(_word_key(key), _word_key(key))
        options[key] = value.strip()
    return next_tokens, options


def _split_tokens(text):
    try:
        return shlex.split(text)
    except ValueError:
        return str(text or '').split()


def _command_key(token):
    value = str(token or '').strip().casefold()
    if value.startswith('/'):
        return value.split('@', 1)[0]
    return _word_key(value)


def _word_key(value):
    value = str(value or '').strip().casefold().replace('ё', 'е')
    return re.sub(r'[^0-9a-zа-я_-]+', '', value)


def _normalize_text(value):
    value = str(value or '').casefold().replace('ё', 'е')
    value = re.sub(r'[^0-9a-zа-я#]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def _join_tokens(tokens):
    return ' '.join(str(token).strip() for token in tokens if str(token).strip()).strip()


def _looks_like_amount(value):
    value = str(value or '').strip()
    return bool(re.match(r'^[+-]?\d+(?:[.,]\d{1,2})?$', value))


def _parse_money(value):
    try:
        return Decimal(str(value).strip().replace(',', '.')).quantize(MONEY)
    except Exception:
        raise ValueError(f'Некорректная сумма: {value}.')


def _parse_optional_money(value, field_name):
    if value is None or str(value).strip() == '':
        return None
    amount = _parse_money(value)
    if amount < 0:
        raise ValueError(f'{field_name} не может быть отрицательным.')
    return amount


def _parse_optional_date(value):
    if value is None or str(value).strip() == '':
        return None
    return _parse_date_value(value)


def _try_parse_date_value(value):
    try:
        return _parse_date_value(value)
    except ValueError:
        return None


def _parse_date_value(value):
    text = str(value or '').strip().casefold().replace('ё', 'е')
    if text in {'сегодня', 'today'}:
        return date.today()
    if text in {'вчера', 'yesterday'}:
        return date.today() - timedelta(days=1)

    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    if re.match(r'^\d{1,2}\.\d{1,2}$', text):
        day_text, month_text = text.split('.', 1)
        try:
            return date(date.today().year, int(month_text), int(day_text))
        except ValueError:
            pass

    raise ValueError(f'Не понял дату: {value}. Используйте формат 2026-07-29 или 29.07.')


def _format_money(value):
    amount = Decimal(str(value or 0)).quantize(MONEY)
    if amount == amount.to_integral():
        return f'{int(amount):,}'.replace(',', ' ') + ' руб.'
    return f'{amount:,.2f}'.replace(',', ' ').replace('.', ',') + ' руб.'


def _format_date(value):
    if not value:
        return 'не указана'
    return value.strftime('%d.%m.%Y')


def _effective_due_date(debt, today):
    if hasattr(debt, 'effective_next_payment_date'):
        return debt.effective_next_payment_date(today)
    return debt.next_payment_date


def _is_truthy(value):
    return str(value).strip().casefold() in {'1', 'true', 'yes', 'on', 'да', 'досрочно'}


def _is_rate_limited(telegram_id, chat_id):
    try:
        limit = int(current_app.config.get('TELEGRAM_BOT_RATE_LIMIT_PER_MINUTE', 20))
    except (TypeError, ValueError):
        limit = 20
    if limit <= 0:
        return False

    now = time.monotonic()
    window_start = now - 60
    key = (int(telegram_id), int(chat_id or telegram_id))
    timestamps = [item for item in _RATE_LIMIT_BUCKETS.get(key, []) if item >= window_start]
    if len(timestamps) >= limit:
        _RATE_LIMIT_BUCKETS[key] = timestamps
        return True
    timestamps.append(now)
    _RATE_LIMIT_BUCKETS[key] = timestamps

    if len(_RATE_LIMIT_BUCKETS) > 1000:
        for bucket_key, bucket_timestamps in list(_RATE_LIMIT_BUCKETS.items()):
            fresh = [item for item in bucket_timestamps if item >= window_start]
            if fresh:
                _RATE_LIMIT_BUCKETS[bucket_key] = fresh
            else:
                _RATE_LIMIT_BUCKETS.pop(bucket_key, None)
    return False
