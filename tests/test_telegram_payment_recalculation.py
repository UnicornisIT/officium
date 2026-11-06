import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from flask import g

from app import create_app
from app.models import Debt, TelegramConversationState, User
from app.services.telegram_bot_service import _finish_conversation
from extensions import db


@pytest.mark.parametrize('days_ahead', [0, 1])
def test_payment_reply_uses_recalculated_balance(days_ahead):
    payment_date = date.today() + timedelta(days=days_ahead)
    app = create_app(
        {
            'TESTING': True,
            'SECRET_KEY': 'payment-test',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        }
    )
    with app.app_context():
        db.create_all()
        try:
            user = User(telegram_id=123456, username='payment_test', role='user')
            db.session.add(user)
            db.session.flush()
            debt = Debt(
                user_id=user.id,
                bank_name='Сбер',
                product_name='Кредит',
                debt_type='consumer_credit',
                total_amount=Decimal('25000'),
                remaining_amount=Decimal('25000'),
                interest_rate=0,
                accrual_start_date=date(2026, 1, 1),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            state = TelegramConversationState(
                telegram_id=user.telegram_id,
                chat_id=user.telegram_id,
                flow='payment',
                step='confirm',
                expires_at=datetime(2030, 1, 1),
                data=json.dumps(
                    {
                        'debt_id': debt.id,
                        'amount': '2500',
                        'payment_date': payment_date.isoformat(),
                    }
                ),
            )
            db.session.add(state)
            db.session.commit()
            assert debt.total_debt() == Decimal('25000')
            old_keys = set(g._debt_state_cache)

            if days_ahead:
                with pytest.raises(ValueError, match='фактическую дату оплаты'):
                    _finish_conversation(user, user.telegram_id, state)
                assert debt.remaining_amount == Decimal('25000')
                assert debt.payments == []
                return

            result = _finish_conversation(user, user.telegram_id, state)

            assert '22 500' in result.reply_text
            assert '25 000' not in result.reply_text
            assert not old_keys.intersection(g._debt_state_cache)
            db.session.expire_all()
            assert debt.remaining_amount == Decimal('22500')
            db.session.remove()
            assert TelegramConversationState.query.count() == 0
        finally:
            db.session.remove()
            db.drop_all()
