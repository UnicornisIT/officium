import pytest

from app import create_app
from app.models import Debt, User
from extensions import db


@pytest.fixture
def client():
    application = create_app(
        {
            'TESTING': True,
            'SECRET_KEY': 'debt-bank-search-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        }
    )
    with application.app_context():
        db.create_all()
        owner = User(telegram_id=19002, username='bank-search-owner', role='user')
        db.session.add(owner)
        db.session.flush()
        for bank_name in ('Сбер 100%', 'Сбер 100_USD', 'Альфа', r'Банк\USD', r'Банк\%'):
            db.session.add(
                Debt(
                    user_id=owner.id,
                    bank_name=bank_name,
                    product_name='Кредит',
                    debt_type='consumer_credit',
                    total_amount=1000,
                    remaining_amount=1000,
                    interest_rate=0,
                    penalty_rate=0,
                    status='active',
                )
            )
        db.session.commit()
        test_client = application.test_client()
        with test_client.session_transaction() as session:
            session['_user_id'] = str(owner.id)
            session['_fresh'] = True
        yield test_client
        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize(
    'term,expected',
    [
        ('%', {'Сбер 100%', r'Банк\%'}),
        ('_', {'Сбер 100_USD'}),
        ('\\', {r'Банк\USD', r'Банк\%'}),
        (r'\%', {r'Банк\%'}),
        ('100%', {'Сбер 100%'}),
        ('100_USD', {'Сбер 100_USD'}),
        ('Сбер', {'Сбер 100%', 'Сбер 100_USD'}),
        ('missing%', set()),
        ('', {'Сбер 100%', 'Сбер 100_USD', 'Альфа', r'Банк\USD', r'Банк\%'}),
    ],
)
def test_bank_search_treats_wildcards_literally(client, term, expected):
    response = client.get('/api/debts', query_string={'bank': term})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert {debt['bank_name'] for debt in payload['debts']} == expected
    assert len(payload['debts']) == len(expected)
