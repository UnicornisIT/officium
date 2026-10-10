import unittest
from datetime import date, datetime
from pathlib import Path

from app import create_app
from app.admin_presenters import (
    admin_event_label,
    admin_field_label,
    admin_role_label,
    admin_status_label,
)
from app.models import ActivityLog, Debt, Payment, User
from extensions import db


class AdminInterfaceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'admin-interface-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
            'ENVIRONMENT': 'development',
            'TEST_USER_ENABLED': False,
            'SERVER_UPDATE_ENABLED': False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            superadmin = User(
                telegram_id=-7001,
                username='root_admin',
                first_name='Мария',
                last_name='Соколова',
                role='superadmin',
                login_count=14,
            )
            admin = User(
                telegram_id=-7002,
                username='support_admin',
                first_name='Илья',
                role='admin',
                login_count=5,
            )
            blocked = User(
                telegram_id=-7003,
                username='blocked_user',
                first_name='Павел',
                role='user',
                is_blocked=True,
            )
            db.session.add_all([superadmin, admin, blocked])
            for index in range(24):
                db.session.add(User(
                    telegram_id=-8000 - index,
                    username=f'client_{index:02d}',
                    first_name=f'Клиент {index:02d}',
                    role='user',
                    login_count=index,
                ))
            db.session.flush()
            debt = Debt(
                user_id=blocked.id,
                bank_name='Северный банк',
                debt_type='consumer_credit',
                product_name='Потребительский кредит',
                total_amount=120000,
                remaining_amount=90000,
                minimum_payment=10000,
                interest_rate=18,
                next_payment_date=date(2026, 11, 5),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            db.session.add(Payment(
                debt_id=debt.id,
                amount=10000,
                principal_amount=8500,
                interest_amount=1500,
                fee_amount=0,
                payment_date=date(2026, 10, 5),
                remaining_after_payment=90000,
            ))
            db.session.add_all([
                ActivityLog(
                    user_id=superadmin.id,
                    action='Изменил настройки приложения',
                    entity_type='setting',
                    description='Обновлены системные настройки',
                    ip_address='127.0.0.1',
                    user_agent='Admin UI Test',
                    created_at=datetime(2026, 10, 10, 9, 0),
                ),
                ActivityLog(
                    user_id=admin.id,
                    action='Заблокировал пользователя',
                    entity_type='user',
                    entity_id=blocked.id,
                    description='Пользователь заблокирован',
                    created_at=datetime(2026, 10, 10, 8, 30),
                ),
            ])
            db.session.commit()
            self.superadmin_id = superadmin.id
            self.admin_id = admin.id
            self.blocked_id = blocked.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True

    def test_administrative_pages_use_shared_shell_and_human_navigation(self):
        self.login(self.superadmin_id)

        for path, heading in (
            ('/admin', 'Общая статистика'),
            ('/admin/users', 'Учетные записи'),
            ('/admin/logs', 'Журнал действий'),
            ('/admin/finance', 'Долговые обязательства'),
            ('/admin/system', 'Состояние системы'),
            ('/admin/settings', 'Настройки приложения'),
            ('/admin/dictionaries', 'Справочники'),
            ('/admin/export', 'Экспорт данных'),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                html = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(heading, html)
                self.assertIn('Административная панель', html)
                self.assertIn('images/brand/officium-mark.svg', html)
                self.assertIn('css/admin.css', html)

    def test_ordinary_admin_sees_only_allowed_sections(self):
        self.login(self.admin_id)

        dashboard = self.client.get('/admin').get_data(as_text=True)
        self.assertIn('Пользователи', dashboard)
        self.assertIn('Журнал действий', dashboard)
        self.assertNotIn('href="/admin/settings"', dashboard)
        self.assertNotIn('href="/admin/system"', dashboard)
        self.assertEqual(self.client.get('/admin/system').status_code, 302)
        self.assertEqual(self.client.get('/admin/export').status_code, 302)

    def test_users_support_search_sort_status_and_pagination(self):
        self.login(self.superadmin_id)

        first_page = self.client.get('/admin/users?sort=name')
        second_page = self.client.get('/admin/users?page=2&sort=name')
        filtered = self.client.get('/admin/users?status=blocked&q=blocked')

        self.assertEqual(first_page.status_code, 200)
        self.assertIn('Страница 1 из 2', first_page.get_data(as_text=True))
        self.assertIn('Страница 2 из 2', second_page.get_data(as_text=True))
        filtered_html = filtered.get_data(as_text=True)
        self.assertIn('blocked_user', filtered_html)
        self.assertIn('Заблокирован', filtered_html)
        self.assertIn('Поле базы данных: telegram_id', first_page.get_data(as_text=True))

    def test_logs_filters_and_hide_technical_details_from_ordinary_admin(self):
        self.login(self.admin_id)

        response = self.client.get('/admin/logs', query_string={
            'user': self.admin_id,
            'event': 'Заблокировал пользователя',
            'date_from': '2026-10-10',
            'date_to': '2026-10-10',
        })
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Пользователь заблокирован', html)
        self.assertNotIn('Технические сведения', html)
        self.assertNotIn('Admin UI Test', html)

    def test_superadmin_can_view_technical_log_details(self):
        self.login(self.superadmin_id)

        html = self.client.get('/admin/logs').get_data(as_text=True)

        self.assertIn('Технические сведения', html)
        self.assertIn('Поле базы данных: ip_address', html)
        self.assertIn('Admin UI Test', html)

    def test_finance_tabs_render_real_debt_and_payment_fields(self):
        self.login(self.admin_id)

        debts = self.client.get('/admin/finance?q=Северный').get_data(as_text=True)
        payments = self.client.get('/admin/finance?section=payments&q=Северный').get_data(as_text=True)

        self.assertIn('Потребительский кредит', debts)
        self.assertIn('90000.00 ₽', debts)
        self.assertIn('10000.00 ₽', payments)
        self.assertIn('Остаток после платежа', payments)

    def test_test_impersonation_is_hidden_and_unavailable_in_production(self):
        self.login(self.superadmin_id)
        self.app.config.update(ENVIRONMENT='production', TEST_USER_ENABLED=False)

        dashboard = self.client.get('/admin').get_data(as_text=True)
        response = self.client.post('/admin/impersonate/test')

        self.assertNotIn('Тестовый пользователь', dashboard)
        self.assertEqual(response.status_code, 404)

    def test_test_impersonation_is_explicitly_enabled_only_for_development(self):
        self.login(self.superadmin_id)
        self.app.config.update(ENVIRONMENT='staging', TEST_USER_ENABLED=True)

        staging_dashboard = self.client.get('/admin').get_data(as_text=True)
        staging_response = self.client.post('/admin/impersonate/test')

        self.assertNotIn('Тестовый пользователь', staging_dashboard)
        self.assertEqual(staging_response.status_code, 404)

        self.app.config.update(ENVIRONMENT='development', TEST_USER_ENABLED=True)

        dashboard = self.client.get('/admin').get_data(as_text=True)

        self.assertIn('Тестовый пользователь', dashboard)

    def test_presenters_translate_known_values_and_preserve_unknown_values(self):
        self.assertEqual(admin_field_label('telegram_id'), 'Идентификатор Telegram')
        self.assertEqual(admin_role_label('superadmin'), 'Супер-администратор')
        self.assertEqual(admin_status_label('healthy'), 'Работает')
        self.assertEqual(admin_event_label('Заблокировал пользователя'), 'Пользователь заблокирован')
        self.assertEqual(admin_status_label('mystery'), 'Неизвестное значение статуса: mystery')

    def test_admin_design_system_covers_dark_mobile_and_reduced_motion(self):
        repository_root = Path(__file__).resolve().parents[1]
        css = (repository_root / 'static' / 'css' / 'admin.css').read_text(encoding='utf-8')
        base = (repository_root / 'templates' / 'base.html').read_text(encoding='utf-8')

        self.assertIn('html[data-theme="dark"] .admin-page', css)
        self.assertIn('@media (max-width: 767.98px)', css)
        self.assertIn('@media (prefers-reduced-motion: reduce)', css)
        self.assertIn(':focus-visible', css)
        self.assertIn('favicon.svg', base)
        self.assertIn('apple-touch-icon.png', base)


if __name__ == '__main__':
    unittest.main()
