"""Debt mutation responses must reflect writes within the same request."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from flask import g

from app import create_app
from app.models import Debt, User
from app.services.debt_accrual_service import (
    cached_debt_state,
    invalidate_debt_state_cache,
)
from app.services.payment_service import add_payment
from extensions import db


@pytest.fixture
def app():
    application = create_app(
        {
            'TESTING': True,
            'SECRET_KEY': 'debt-cache-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        }
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def make_debt():
    owner = User(telegram_id=19001, username='debt-cache-owner', role='user')
    db.session.add(owner)
    db.session.flush()
    debt = Debt(
        user_id=owner.id,
        bank_name='Банк',
        product_name='Кредит',
        debt_type='consumer_credit',
        total_amount=Decimal('25000'),
        remaining_amount=Decimal('25000'),
        interest_rate=0,
        penalty_rate=0,
        minimum_payment=Decimal('2500'),
        accrual_start_date=date.today(),
        next_payment_date=date.today() + timedelta(days=30),
        status='active',
    )
    db.session.add(debt)
    db.session.commit()
    return debt


@pytest.mark.parametrize(
    'operation,expected',
    [
        ('add', 22500),
        ('update_payment', 22000),
        ('delete_payment', 25000),
        ('accept_bank_remaining', 23000),
        ('update_debt', 20000),
        ('split_purchase', 26000),
        ('restore', 25000),
    ],
)
def test_mutation_serializes_fresh_debt(app, operation, expected):
    debt = make_debt()
    payment = None
    if operation in ('update_payment', 'delete_payment', 'accept_bank_remaining'):
        payment = add_payment(debt, '2500', bank_remaining_after_payment='23000')
    if operation == 'split_purchase':
        debt.debt_type = 'split'
    if operation == 'restore':
        debt.status = 'archived'
    db.session.commit()
    debt_id = debt.id
    payment_id = payment.id if payment else None
    route_module = (
        'payments'
        if operation
        in (
            'add',
            'update_payment',
            'delete_payment',
            'accept_bank_remaining',
        )
        else 'debts'
    )
    path = f'/api/debts/{debt_id}'
    method, body = 'POST', {}
    if operation == 'add':
        path += '/payments'
        body = {'amount': '2500'}
    elif operation in ('update_payment', 'delete_payment', 'accept_bank_remaining'):
        path += f'/payments/{payment_id}'
        method = 'PUT' if operation == 'update_payment' else 'DELETE'
        body = {'amount': '3000', 'payment_date': date.today().isoformat()}
        if operation == 'accept_bank_remaining':
            path += '/accept-bank-remaining'
            method = 'POST'
    elif operation == 'update_debt':
        method, body = 'PUT', {'remaining_amount': '20000'}
    elif operation == 'split_purchase':
        path += '/split-purchases'
        body = {'amount': '1000', 'purchase_date': date.today().isoformat()}
    else:
        path += '/restore'

    primed = []

    def prime_cache(requested_id):
        assert requested_id == debt_id
        primed.append(debt.to_dict())
        cached_debt_state(debt, date.today() + timedelta(days=1))
        return debt

    with app.test_request_context(path, method=method, json=body):
        with (
            patch(f'app.routes.{route_module}.get_user_debt', side_effect=prime_cache),
            patch('app.routes.debts.is_local_test_user', return_value=False),
            patch('app.routes.debts.get_setting', return_value='true'),
            patch.object(db.session, 'refresh', wraps=db.session.refresh) as refresh,
        ):
            response = app.view_functions[
                app.url_map.bind('').match(path, method=method)[0]
            ](**app.url_map.bind('').match(path, method=method)[1])
        if isinstance(response, tuple):
            response, status = response
            assert status == 201
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['success'], payload
        assert len(primed) == 1
        assert payload['debt']['remaining_amount'] == expected
        assert payload['debt']['total_debt'] == expected
        if operation != 'restore':
            assert payload['debt']['remaining_amount'] != primed[0]['remaining_amount']
        refresh.assert_called_once_with(debt)
        if operation == 'restore':
            assert payload['debt']['status'] == 'active'
        invalidate_debt_state_cache()
        assert payload['debt'] == debt.to_dict()


def test_invalidation_removes_all_dates_only_for_target_debt(app):
    debt = make_debt()
    other = Debt(id=debt.id + 1)
    today = date.today()
    cached_debt_state(debt, today)
    cached_debt_state(debt, today + timedelta(days=1))
    other_key = (id(other), other.id, today)
    g._debt_state_cache[other_key] = {'sentinel': True}

    invalidate_debt_state_cache(debt)

    assert g._debt_state_cache == {other_key: {'sentinel': True}}
    invalidate_debt_state_cache()
    assert g._debt_state_cache == {}


def test_invalidation_without_application_context():
    invalidate_debt_state_cache()
