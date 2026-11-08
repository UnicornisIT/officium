import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app.services import monthly_expenses_service, vehicle_service
from app.services.debt_schedule_service import split_schedule_rows
from app.time_utils import account_calendar, financial_today, utc_now
from tests.test_credit_bank_regression import bank_debt


class CalendarPolicyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['APP_TIMEZONE'] = 'UTC'

    def test_monthly_and_vehicle_dates_follow_account(self):
        owner = SimpleNamespace(timezone='Europe/Moscow')
        instant = datetime(2026, 11, 4, 23, 13, tzinfo=timezone.utc)
        with (
            self.app.app_context(),
            patch('app.time_utils.utc_now', return_value=instant),
        ):
            self.assertEqual(financial_today(owner), date(2026, 11, 5))
            self.assertEqual(monthly_expenses_service._today(owner), date(2026, 11, 5))
            self.assertEqual(vehicle_service.today(owner), date(2026, 11, 5))

    def test_explicit_split_checkpoint_does_not_read_clock(self):
        debt = bank_debt(id=None, debt_type='split', next_payment_date=None)
        with patch('app.time_utils.utc_now', side_effect=AssertionError('clock read')):
            rows = split_schedule_rows(debt, today=date(2026, 11, 5))
        self.assertEqual(rows['rows'][0]['payment_date'], '2026-11-05')

    def test_invalid_account_zone_falls_back_to_application_zone(self):
        self.app.config['APP_TIMEZONE'] = 'Europe/Moscow'
        with self.app.app_context():
            self.assertEqual(
                financial_today(
                    SimpleNamespace(timezone='Invalid/Zone'),
                    now=datetime(2026, 11, 4, 23, 13, tzinfo=timezone.utc),
                ),
                date(2026, 11, 5),
            )

    def test_midnight_year_boundary_and_tallinn_dst(self):
        cases = [
            ('Europe/Moscow', '2026-12-31T20:59:59+00:00', '2026-12-31'),
            ('Europe/Moscow', '2026-12-31T21:00:00+00:00', '2027-01-01'),
            ('UTC', '2026-12-31T23:59:59+00:00', '2026-12-31'),
            ('UTC', '2027-01-01T00:00:00+00:00', '2027-01-01'),
            ('Europe/Tallinn', '2026-03-29T00:59:59+00:00', '2026-03-29'),
            ('Europe/Tallinn', '2026-03-29T01:00:00+00:00', '2026-03-29'),
            ('Europe/Tallinn', '2026-10-25T00:59:59+00:00', '2026-10-25'),
            ('Europe/Tallinn', '2026-10-25T01:00:00+00:00', '2026-10-25'),
            ('Europe/Tallinn', '2026-03-29T21:30:00+00:00', '2026-03-30'),
            ('Europe/Tallinn', '2026-10-25T21:30:00+00:00', '2026-10-25'),
        ]
        for zone, instant, expected in cases:
            with self.subTest(zone=zone, instant=instant):
                self.assertEqual(
                    financial_today(
                        SimpleNamespace(timezone=zone),
                        now=datetime.fromisoformat(instant),
                    ),
                    date.fromisoformat(expected),
                )

    def test_injected_clock_keeps_timestamps_utc_and_bot_calendar_scoped(self):
        instant = datetime.fromisoformat('2026-11-05T02:13:00+03:00')
        self.app.config['UTC_NOW_PROVIDER'] = lambda: instant

        @account_calendar
        def bot_date(user):
            return financial_today()

        with self.app.app_context():
            self.assertEqual(
                utc_now(), datetime(2026, 11, 4, 23, 13, tzinfo=timezone.utc)
            )
            self.assertEqual(utc_now().tzinfo, timezone.utc)
            self.assertEqual(
                bot_date(SimpleNamespace(timezone='Europe/Moscow')), date(2026, 11, 5)
            )
            self.assertEqual(financial_today(), date(2026, 11, 4))


class CalendarPersistenceTests(unittest.TestCase):
    def setUp(self):
        from app import create_app
        from app.models import User
        from extensions import db

        self.app = create_app(
            {
                'TESTING': True,
                'SECRET_KEY': 'calendar-test',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_ENGINE_OPTIONS': {},
                'WTF_CSRF_ENABLED': False,
                'APP_TIMEZONE': 'UTC',
                'UTC_NOW_PROVIDER': lambda: datetime(
                    2026, 12, 31, 22, 30, tzinfo=timezone.utc
                ),
            }
        )
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.user = User(telegram_id=91234, timezone='Europe/Moscow')
        self.utc_user = User(telegram_id=91235, timezone='UTC')
        db.session.add_all([self.user, self.utc_user])
        db.session.commit()

    def tearDown(self):
        from extensions import db

        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_background_generation_uses_each_owners_month_and_preserves_dates(self):
        from app.models import Expense
        from extensions import db

        for user in (self.user, self.utc_user):
            db.session.add(
                Expense(
                    user_id=user.id,
                    amount=100,
                    title='Rent',
                    category='other',
                    expense_date=date(2026, 12, 5),
                    is_monthly=True,
                    monthly_group_id=str(user.id),
                    generated_for_month='2026-12',
                )
            )
        db.session.commit()
        monthly_expenses_service.create_monthly_expenses_for_new_month()
        db.session.expire_all()
        self.assertEqual(
            [
                e.expense_date
                for e in Expense.query.filter_by(user_id=self.user.id).order_by(
                    Expense.expense_date
                )
            ],
            [date(2026, 12, 5), date(2027, 1, 5)],
        )
        self.assertEqual(
            [e.expense_date for e in Expense.query.filter_by(user_id=self.utc_user.id)],
            [date(2026, 12, 5)],
        )
        with patch('app.time_utils.utc_now', side_effect=AssertionError('clock read')):
            result = monthly_expenses_service.generate_monthly_expenses(
                target_month='2027-01'
            )
        self.assertEqual(result['errors'], 0)

    def test_web_api_and_mini_app_share_date_debt_and_daily_budget(self):
        from flask import template_rendered
        from app.models import Income
        from app.services.finance_summary_service import get_finance_summary
        from extensions import db

        debt = bank_debt(user_id=self.user.id)
        db.session.add_all(
            [
                debt,
                Income(
                    user_id=self.user.id,
                    amount=31000,
                    category='salary',
                    income_date=date(2027, 1, 1),
                ),
            ]
        )
        db.session.commit()
        expected = get_finance_summary(self.user.id)
        contexts = []

        def capture(sender, template, context, **extra):
            if template.name == 'index.html':
                contexts.append(context)

        template_rendered.connect(capture, self.app)
        self.addCleanup(template_rendered.disconnect, capture, self.app)
        for mini in (False, True):
            client = self.app.test_client()
            with client.session_transaction() as session:
                session['_user_id'] = str(self.user.id)
                session['_fresh'] = True
                session['telegram_mini_app'] = mini
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(
                client.get('/api/calendar-date').get_json()['today'], '2027-01-01'
            )
            data = client.get(f'/api/debts/{debt.id}/payments').get_json()
            self.assertEqual(
                data['debt']['total_debt'], float(expected['total_remaining'])
            )
            self.assertEqual(data['debt']['today'], '2027-01-01')
        self.assertEqual(len(contexts), 2)
        for context in contexts:
            self.assertEqual(context['today'], date(2027, 1, 1))
            self.assertEqual(context['total_remaining'], expected['total_remaining'])
            self.assertEqual(
                context['daily_budget'],
                expected['free_balance'] / expected['days_left'],
            )
