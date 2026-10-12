import unittest
from decimal import Decimal

from app import create_app
from app.models import Debt, Payment, User
from extensions import db


class UserDataIsolationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'idor-test-secret-over-thirty-two-characters',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            owner = User(telegram_id=-9401, username='owner', role='user')
            attacker = User(telegram_id=-9402, username='attacker', role='user')
            db.session.add_all([owner, attacker])
            db.session.flush()
            debt = Debt(
                user_id=owner.id,
                bank_name='Synthetic bank', debt_type='credit_card',
                product_name='Owner card', total_amount=Decimal('1000.00'),
                remaining_amount=Decimal('900.00'), status='active',
            )
            db.session.add(debt)
            db.session.flush()
            own_debt = Debt(
                user_id=attacker.id,
                bank_name='Safe Bank', debt_type='credit_card',
                product_name='Attacker card', total_amount=Decimal('500.00'),
                remaining_amount=Decimal('500.00'), status='active',
            )
            db.session.add(own_debt)
            payment = Payment(
                debt_id=debt.id, amount=Decimal('100.00'),
                principal_amount=Decimal('100.00'), interest_amount=Decimal('0.00'),
                fee_amount=Decimal('0.00'), remaining_after_payment=Decimal('900.00'),
            )
            db.session.add(payment)
            db.session.commit()
            self.attacker_id = attacker.id
            self.debt_id = debt.id
            self.payment_id = payment.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login_as_attacker(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.attacker_id)
            session['_fresh'] = True

    def test_cross_user_debt_and_payment_operations_are_not_discoverable(self):
        self.login_as_attacker()
        requests = (
            self.client.get(f'/api/debts/{self.debt_id}'),
            self.client.get(f'/api/debts/{self.debt_id}/payments'),
            self.client.put(f'/api/debts/{self.debt_id}', json={'comment': 'stolen'}),
            self.client.put(
                f'/api/debts/{self.debt_id}/payments/{self.payment_id}',
                json={'amount': '1', 'payment_date': '2026-10-11'},
            ),
            self.client.delete(f'/api/debts/{self.debt_id}/delete'),
        )
        self.assertTrue(all(response.status_code == 404 for response in requests))
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Debt, self.debt_id))
            self.assertIsNotNone(db.session.get(Payment, self.payment_id))

    def test_bank_filter_uses_bound_literal_input(self):
        self.login_as_attacker()
        ordinary = self.client.get('/api/debts?bank=Safe').get_json()['debts']
        injected = self.client.get("/api/debts?bank=%25%27%20OR%201%3D1%20--").get_json()['debts']
        self.assertEqual(len(ordinary), 1)
        self.assertEqual(injected, [])


if __name__ == '__main__':
    unittest.main()
