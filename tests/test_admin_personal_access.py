"""Administrators use the personal cabinet under their own account and keep the panel."""

import unittest
from decimal import Decimal

from app import create_app
from app.models import Debt, Expense, ImpersonationSession, Income, Payment, User
from app.services import impersonation_service as impersonation
from app.services.personal_access_service import can_use_personal_cabinet
from app.routes.auth import AdminUser
from extensions import db


ADMIN_ROLES = ('admin', 'superadmin')
OTHER_MARKER = 'Чужая карта 7731'
ADMIN_DEBT_MARKER = 'Личный кредит админа 4410'
ADMIN_EXPENSE_MARKER = 'Личный расход админа 5521'
ADMIN_INCOME_MARKER = 'Личный доход админа 6632'

DEBT_BODY = {
    'bank_name': 'Банк',
    'debt_type': 'consumer_credit',
    'product_name': ADMIN_DEBT_MARKER,
    'total_amount': '100000',
    'remaining_amount': '100000',
    'minimum_payment': '5000',
    'interest_rate': '18',
    'next_payment_date': '2026-08-15',
}


def _config(**overrides):
    config = {
        'TESTING': True,
        'ENVIRONMENT': 'testing',
        'SECRET_KEY': 'admin-personal-access-secret-over-32-chars',
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_ENGINE_OPTIONS': {},
        'WTF_CSRF_ENABLED': False,
        'ADMIN_LOGIN_ENABLED': True,
    }
    config.update(overrides)
    return config


class AdminPersonalAccessTestCase(unittest.TestCase):
    csrf_enabled = False

    def setUp(self):
        self.app = create_app(_config(WTF_CSRF_ENABLED=self.csrf_enabled))
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            root = User(telegram_id=1, username='root', role='superadmin')
            helper = User(telegram_id=2, username='helper', role='admin')
            member = User(telegram_id=3, username='member', role='user')
            db.session.add_all([root, helper, member])
            db.session.flush()
            other_debt = Debt(
                user_id=member.id, bank_name='Банк', debt_type='credit_card',
                product_name=OTHER_MARKER, total_amount=Decimal('1000.00'),
                remaining_amount=Decimal('900.00'), status='active',
            )
            db.session.add(other_debt)
            db.session.commit()
            self.ids = {'superadmin': root.id, 'admin': helper.id, 'user': member.id}
            self.other_debt_id = other_debt.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def _session_user(self):
        with self.client.session_transaction() as session:
            return session.get('_user_id')

    def _create_records(self):
        debt = self.client.post('/api/debts', json=DEBT_BODY)
        self.assertEqual(debt.status_code, 201, debt.get_json())
        debt_id = debt.get_json()['debt']['id']
        payment = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '5000', 'payment_date': '2026-08-15',
        })
        self.assertEqual(payment.status_code, 200)
        self.assertTrue(payment.get_json()['success'])
        income = self.client.post('/incomes', data={
            'amount': '70000', 'category': 'salary', 'income_date': '2026-08-01',
            'source': ADMIN_INCOME_MARKER,
        })
        self.assertEqual(income.status_code, 302)
        expense = self.client.post('/expenses', data={
            'amount': '1200', 'category': 'products', 'title': ADMIN_EXPENSE_MARKER,
            'expense_date': '2026-08-02',
        })
        self.assertEqual(expense.status_code, 302)
        return debt_id


class AdminCabinetTestCase(AdminPersonalAccessTestCase):
    def test_admins_open_the_personal_dashboard_without_redirect(self):
        for role in ADMIN_ROLES:
            with self.subTest(role=role):
                self._login(self.ids[role])
                for path in ('/', '/incomes', '/expenses', '/finance', '/financial-plan', '/api/debts'):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200, path)

    def test_admin_records_belong_to_their_own_account(self):
        for role in ADMIN_ROLES:
            with self.subTest(role=role):
                self._login(self.ids[role])
                debt_id = self._create_records()
                with self.app.app_context():
                    owner = self.ids[role]
                    self.assertEqual(db.session.get(Debt, debt_id).user_id, owner)
                    self.assertEqual(Payment.query.filter_by(debt_id=debt_id).count(), 1)
                    self.assertEqual(Income.query.filter_by(user_id=owner).count(), 1)
                    self.assertEqual(Expense.query.filter_by(user_id=owner).count(), 1)

    def test_admins_see_only_their_own_records(self):
        for role in ADMIN_ROLES:
            with self.subTest(role=role):
                self._login(self.ids[role])
                self._create_records()
                debts = self.client.get('/api/debts').get_json()
                names = {debt['product_name'] for debt in debts.get('debts', debts if isinstance(debts, list) else [])}
                self.assertIn(ADMIN_DEBT_MARKER, names)
                self.assertNotIn(OTHER_MARKER, names)
                self.assertNotIn(OTHER_MARKER, self.client.get('/').get_data(as_text=True))
                self.assertEqual(self.client.get(f'/api/debts/{self.other_debt_id}').status_code, 404)
                payment = self.client.post(f'/api/debts/{self.other_debt_id}/payments', json={
                    'amount': '100', 'payment_date': '2026-08-15',
                })
                self.assertEqual(payment.status_code, 404)

        # Each administrator sees one debt: their own, not the other admin's.
        self._login(self.ids['admin'])
        with self.app.app_context():
            self.assertEqual(Debt.query.filter_by(product_name=ADMIN_DEBT_MARKER).count(), 2)
        debts = self.client.get('/api/debts').get_json()
        rows = debts.get('debts', debts if isinstance(debts, list) else [])
        self.assertEqual([row['product_name'] for row in rows], [ADMIN_DEBT_MARKER])

    def test_admin_panel_opens_after_the_cabinet_without_new_login(self):
        for role in ADMIN_ROLES:
            with self.subTest(role=role):
                self._login(self.ids[role])
                self._create_records()
                self.assertEqual(self.client.get('/').status_code, 200)
                self.assertEqual(self.client.get('/admin').status_code, 200)
                self.assertEqual(self.client.get('/').status_code, 200)
                self.assertEqual(self._session_user(), str(self.ids[role]))

    def test_admin_role_stays_out_of_superadmin_sections_after_switching(self):
        self._login(self.ids['admin'])
        self.client.get('/')
        self.client.get('/admin')
        self.client.get('/')

        response = self.client.get('/admin/settings')

        self.assertEqual(response.status_code, 403)

    def test_personal_pages_link_back_to_the_panel_for_admins_only(self):
        for role in ADMIN_ROLES:
            with self.subTest(role=role):
                self._login(self.ids[role])
                html = self.client.get('/').get_data(as_text=True)
                self.assertIn('data-admin-panel-link', html)
                self.assertIn('Панель администратора', html)

        self._login(self.ids['user'])
        html = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('data-admin-panel-link', html)
        self.assertNotIn('Панель администратора', html)

    def test_plain_user_cannot_open_the_panel(self):
        self._login(self.ids['user'])

        response = self.client.get('/admin')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_admin_pages_hide_administrators_financial_records(self):
        self._login(self.ids['superadmin'])
        self._create_records()
        self._login(self.ids['admin'])
        self._create_records()

        for role in ADMIN_ROLES:
            self._login(self.ids[role])
            for path in (
                '/admin', '/admin/users', '/admin/logs',
                f"/admin/users/{self.ids['admin']}", f"/admin/users/{self.ids['superadmin']}",
            ):
                with self.subTest(role=role, path=path):
                    body = self.client.get(path).get_data(as_text=True)
                    for marker in (ADMIN_DEBT_MARKER, ADMIN_EXPENSE_MARKER, ADMIN_INCOME_MARKER, OTHER_MARKER):
                        self.assertNotIn(marker, body)

    def test_role_change_keeps_personal_records(self):
        self._login(self.ids['user'])
        self.client.post('/incomes', data={
            'amount': '1000', 'category': 'salary', 'income_date': '2026-08-01',
            'source': ADMIN_INCOME_MARKER,
        })

        self._login(self.ids['superadmin'])
        response = self.client.post(f"/admin/users/{self.ids['user']}", data={'action': 'set_role', 'role': 'admin'})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(User, self.ids['user']).role, 'admin')

        self._login(self.ids['user'])
        self.assertIn(OTHER_MARKER, self.client.get('/').get_data(as_text=True))
        self.assertIn(ADMIN_INCOME_MARKER, self.client.get('/incomes?month=2026-08').get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Debt.query.filter_by(user_id=self.ids['user']).count(), 1)
            self.assertEqual(Income.query.filter_by(user_id=self.ids['user']).count(), 1)


class EmergencyAdminTestCase(AdminPersonalAccessTestCase):
    def test_emergency_admin_is_sent_back_to_the_panel(self):
        self._login('admin')
        for path in ('/', '/incomes', '/expenses', '/api/debts'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers['Location'], '/admin')
        self.assertEqual(self.client.get('/admin').status_code, 200)

    def test_emergency_admin_cannot_create_financial_records(self):
        self._login('admin')

        responses = [
            self.client.post('/api/debts', json=DEBT_BODY),
            self.client.post('/incomes', data={'amount': '1', 'category': 'salary', 'income_date': '2026-08-01'}),
            self.client.post('/expenses', data={'amount': '1', 'category': 'products', 'title': 'x', 'expense_date': '2026-08-01'}),
            self.client.post(f'/api/debts/{self.other_debt_id}/payments', json={'amount': '1', 'payment_date': '2026-08-01'}),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers['Location'], '/admin')
        with self.app.app_context():
            self.assertEqual(Debt.query.count(), 1)
            self.assertEqual(Income.query.count(), 0)
            self.assertEqual(Expense.query.count(), 0)
            self.assertEqual(Payment.query.count(), 0)

    def test_access_rule(self):
        with self.app.test_request_context():
            self.assertFalse(can_use_personal_cabinet(AdminUser()))
            for role in ('user', 'admin', 'superadmin'):
                self.assertTrue(can_use_personal_cabinet(User(telegram_id=9, role=role)))


class AdminImpersonationTestCase(AdminPersonalAccessTestCase):
    def test_admin_targets_are_rejected(self):
        self._login(self.ids['superadmin'])
        with self.app.app_context():
            other_root = User(telegram_id=4, username='root2', role='superadmin')
            db.session.add(other_root)
            db.session.commit()
            targets = (self.ids['admin'], other_root.id)

        for target_id in targets:
            with self.subTest(target_id=target_id):
                response = self.client.post(
                    f'/admin/impersonate/{target_id}',
                    data={'reason': 'Проверка обращения пользователя'},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self._session_user(), str(self.ids['superadmin']))
        with self.app.app_context():
            self.assertEqual(ImpersonationSession.query.count(), 0)

    def test_service_rejects_admin_targets(self):
        with self.app.app_context():
            root = db.session.get(User, self.ids['superadmin'])
            for role in ADMIN_ROLES:
                with self.subTest(role=role):
                    target = User(telegram_id=10, role=role, is_blocked=False)
                    with self.assertRaises(impersonation.ImpersonationError):
                        impersonation.ensure_can_impersonate(root, target)
            impersonation.ensure_can_impersonate(root, db.session.get(User, self.ids['user']))

    def test_button_is_hidden_for_admin_accounts(self):
        self._login(self.ids['superadmin'])

        detail = self.client.get(f"/admin/users/{self.ids['admin']}").get_data(as_text=True)
        self.assertNotIn('impersonateModal', detail)
        user_detail = self.client.get(f"/admin/users/{self.ids['user']}").get_data(as_text=True)
        self.assertIn('impersonateModal', user_detail)

        listing = self.client.get('/admin/users').get_data(as_text=True)
        self.assertIn(f"/admin/users/{self.ids['user']}#impersonate", listing)
        self.assertNotIn(f"/admin/users/{self.ids['admin']}#impersonate", listing)

    def test_session_ends_when_the_target_becomes_admin(self):
        self._login(self.ids['superadmin'])
        self.client.post(
            f"/admin/impersonate/{self.ids['user']}",
            data={'reason': 'Проверка обращения пользователя'},
        )
        self.assertEqual(self._session_user(), str(self.ids['user']))
        with self.app.app_context():
            db.session.get(User, self.ids['user']).role = 'admin'
            db.session.commit()

        response = self.client.get('/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin')
        self.assertEqual(self._session_user(), str(self.ids['superadmin']))
        with self.client.session_transaction() as session:
            self.assertNotIn('original_admin_id', session)
        with self.app.app_context():
            record = ImpersonationSession.query.one()
            self.assertEqual(record.end_reason, 'role_changed')
            self.assertIsNotNone(record.ended_at)


class AdminLandingTestCase(AdminPersonalAccessTestCase):
    def test_signed_in_admin_lands_in_the_panel_from_login(self):
        for role in ADMIN_ROLES:
            with self.subTest(role=role):
                self._login(self.ids[role])
                self.assertEqual(self.client.get('/login').headers['Location'], '/admin')

        self._login(self.ids['user'])
        self.assertEqual(self.client.get('/login').headers['Location'], '/')


class AdminCabinetCsrfTestCase(AdminPersonalAccessTestCase):
    csrf_enabled = True

    def test_personal_posts_require_csrf_token(self):
        for role in ADMIN_ROLES:
            with self.subTest(role=role):
                self._login(self.ids[role])
                responses = [
                    self.client.post('/api/debts', json=DEBT_BODY),
                    self.client.post('/incomes', data={'amount': '1', 'category': 'salary', 'income_date': '2026-08-01'}),
                    self.client.post('/expenses', data={'amount': '1', 'category': 'products', 'title': 'x', 'expense_date': '2026-08-01'}),
                ]
                for response in responses:
                    self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertEqual(Debt.query.count(), 1)
            self.assertEqual(Income.query.count(), 0)
            self.assertEqual(Expense.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
