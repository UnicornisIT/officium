"""Goals and the emergency fund as transfers, the goals page and month totals."""

import re
import unittest
from datetime import date
from decimal import Decimal

from app import create_app
from app.models import (
    ActivityLog,
    Debt,
    EmergencyFundTransaction,
    Expense,
    FinancialGoal,
    FinancialGoalTransaction,
    FinancialPlanPreference,
    Income,
    Payment,
    User,
)
from app.services import goal_service
from app.services.finance_summary_service import get_finance_summary
from app.services.financial_plan_service import build_financial_plan
from app.services.telegram_bot_service import _build_summary
from app.utils import format_currency
from extensions import db


def _config(**overrides):
    config = {
        'TESTING': True,
        'ENVIRONMENT': 'testing',
        'SECRET_KEY': 'goals-test-secret-over-32-characters',
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_ENGINE_OPTIONS': {},
        'WTF_CSRF_ENABLED': False,
    }
    config.update(overrides)
    return config


def _text(html):
    return re.sub(r'\s+', ' ', html.replace('\xa0', ' '))


class GoalTestBase(unittest.TestCase):
    csrf = False

    def setUp(self):
        self.app = create_app(_config(WTF_CSRF_ENABLED=self.csrf))
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            owner = User(telegram_id=1, username='owner', first_name='Ольга', role='user')
            stranger = User(telegram_id=2, username='stranger', role='user')
            admin = User(telegram_id=3, username='admin', role='admin')
            db.session.add_all([owner, stranger, admin])
            db.session.flush()
            goal = FinancialGoal(
                user_id=owner.id, name='Отпуск-7319', target_amount=Decimal('60000'),
                monthly_contribution=Decimal('10000'), priority=2,
            )
            foreign_goal = FinancialGoal(
                user_id=stranger.id, name='Чужая цель-4471', target_amount=Decimal('1000'),
                monthly_contribution=Decimal('100'), priority=2,
            )
            db.session.add_all([goal, foreign_goal])
            db.session.commit()
            self.ids = {
                'owner': owner.id, 'stranger': stranger.id, 'admin': admin.id,
                'goal': goal.id, 'foreign_goal': foreign_goal.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, key='owner'):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.ids[key])
            session['_fresh'] = True

    def add(self, transaction_type, amount, day, goal=None, comment=None):
        key = goal_service.EMERGENCY_KEY if goal == 'emergency' else str(goal or self.ids['goal'])
        with self.app.app_context():
            ref = goal_service.get_goal_ref(self.ids['owner'], key)
            return goal_service.add_operation(ref, transaction_type, Decimal(amount), day, comment).id


class GoalOperationsTestCase(GoalTestBase):
    def test_operations_create_no_income_or_expense(self):
        self.login()
        goal_id = self.ids['goal']
        today = date.today().isoformat()
        responses = [
            self.client.post(f'/financial-plan/goals/{goal_id}/transactions', data={
                'transaction_type': 'deposit', 'amount': '5000', 'transaction_date': today,
            }),
            self.client.post(f'/financial-plan/goals/{goal_id}/transactions', data={
                'transaction_type': 'withdrawal', 'amount': '1000', 'transaction_date': today,
            }),
            self.client.post('/financial-plan/emergency-fund', data={
                'transaction_type': 'deposit', 'amount': '2000', 'transaction_date': today,
            }),
            self.client.post('/financial-plan/emergency-fund', data={
                'transaction_type': 'withdrawal', 'amount': '500', 'transaction_date': today,
            }),
        ]

        self.assertTrue(all(response.status_code == 302 for response in responses))
        with self.app.app_context():
            self.assertEqual(FinancialGoalTransaction.query.count(), 2)
            self.assertEqual(EmergencyFundTransaction.query.count(), 2)
            self.assertEqual(Income.query.count(), 0)
            self.assertEqual(Expense.query.count(), 0)

    def test_history_has_balance_after_each_operation(self):
        self.add('deposit', '10000', date(2026, 7, 5), comment='Первый')
        self.add('deposit', '5000', date(2026, 8, 1))
        self.add('withdrawal', '3000', date(2026, 8, 20), comment='Билеты')

        with self.app.app_context():
            ref = goal_service.get_goal_ref(self.ids['owner'], self.ids['goal'])
            history = goal_service.operation_history(ref)
            months = goal_service.monthly_summary(history)

        self.assertEqual(
            [(row['transaction_type'], row['amount'], row['balance_after']) for row in history],
            [
                ('withdrawal', Decimal('3000.00'), Decimal('12000.00')),
                ('deposit', Decimal('5000.00'), Decimal('15000.00')),
                ('deposit', Decimal('10000.00'), Decimal('10000.00')),
            ],
        )
        self.assertEqual(history[0]['comment'], 'Билеты')
        self.assertEqual(
            [(m['month_start'], m['deposited'], m['withdrawn'], m['net'], m['balance_end']) for m in months],
            [
                (date(2026, 8, 1), Decimal('5000.00'), Decimal('3000.00'), Decimal('2000.00'), Decimal('12000.00')),
                (date(2026, 7, 1), Decimal('10000.00'), Decimal('0.00'), Decimal('10000.00'), Decimal('10000.00')),
            ],
        )

    def test_history_never_goes_negative(self):
        deposit_id = self.add('deposit', '1000', date(2026, 8, 10))
        withdrawal_id = self.add('withdrawal', '800', date(2026, 8, 12))

        with self.app.app_context():
            ref = goal_service.get_goal_ref(self.ids['owner'], self.ids['goal'])
            # A withdrawal before the money arrived is rejected even if the total is positive.
            with self.assertRaisesRegex(ValueError, 'Нельзя снять больше'):
                goal_service.add_operation(ref, 'withdrawal', Decimal('100'), date(2026, 8, 1))
            with self.assertRaisesRegex(ValueError, 'Нельзя снять больше'):
                goal_service.update_operation(ref, deposit_id, 'deposit', Decimal('500'), date(2026, 8, 10))
            with self.assertRaisesRegex(ValueError, 'Сначала удалите связанные снятия'):
                goal_service.delete_operation(ref, deposit_id)
            # The rejected edit left the stored deposit untouched.
            self.assertEqual(db.session.get(FinancialGoalTransaction, deposit_id).amount, Decimal('1000.00'))

            goal_service.update_operation(ref, withdrawal_id, 'withdrawal', Decimal('300'), date(2026, 8, 12), 'Меньше')
            goal_service.update_operation(ref, deposit_id, 'deposit', Decimal('300'), date(2026, 8, 10))
            self.assertEqual(goal_service.operation_history(ref)[0]['balance_after'], Decimal('0.00'))

    def test_forecast_uses_recent_pace_and_planned_contribution(self):
        today = date(2026, 10, 16)
        history = [
            {'transaction_type': 'deposit', 'amount': Decimal('9000'), 'transaction_date': date(2026, 7, 3)},
            {'transaction_type': 'deposit', 'amount': Decimal('6000'), 'transaction_date': date(2026, 9, 3)},
            {'transaction_type': 'withdrawal', 'amount': Decimal('3000'), 'transaction_date': date(2026, 9, 9)},
            # The current month is not part of the pace.
            {'transaction_type': 'deposit', 'amount': Decimal('50000'), 'transaction_date': date(2026, 10, 1)},
        ]

        result = goal_service.forecast(Decimal('20000'), Decimal('50000'), history, Decimal('10000'), today)

        self.assertEqual(result['pace'], Decimal('4000.00'))  # (9000 + 6000 − 3000) / 3
        self.assertEqual(result['gap'], Decimal('30000.00'))
        self.assertEqual(result['pace_months_left'], 8)
        self.assertEqual(result['pace_date'], date(2027, 6, 1))
        self.assertEqual(result['planned_months_left'], 3)
        self.assertEqual(result['planned_date'], date(2027, 1, 1))

        stalled = goal_service.forecast(Decimal('100'), Decimal('500'), [], Decimal('0'), today)
        self.assertIsNone(stalled['pace_date'])
        self.assertIsNone(stalled['planned_date'])
        reached = goal_service.forecast(Decimal('500'), Decimal('500'), [], Decimal('0'), today)
        self.assertTrue(reached['is_reached'])


class GoalPagesTestCase(GoalTestBase):
    def test_goals_page_lists_fund_and_own_goals_only(self):
        self.add('deposit', '15000', date(2026, 8, 1))
        self.login()

        response = self.client.get('/goals')
        html = _text(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-goal-card="emergency"', html)
        self.assertIn(f'data-goal-card="{self.ids["goal"]}"', html)
        self.assertIn('Отпуск-7319', html)
        self.assertIn('15 000 ₽', html)
        self.assertIn('60 000 ₽', html)
        self.assertNotIn('Чужая цель-4471', html)

    def test_goal_detail_shows_history_month_summary_and_forecast(self):
        self.add('deposit', '15000', date(2026, 8, 1), comment='Аванс')
        self.add('withdrawal', '2500', date(2026, 8, 9))
        self.login()

        html = _text(self.client.get(f'/goals/{self.ids["goal"]}').get_data(as_text=True))

        self.assertIn('История операций', html)
        self.assertIn('09.08.2026', html)
        self.assertIn('Снятие', html)
        self.assertIn('12 500 ₽', html)  # balance after the withdrawal
        self.assertIn('Аванс', html)
        self.assertIn('data-month="2026-08"', html)
        self.assertIn('data-goal-forecast', html)
        self.assertIn(f'/goals/{self.ids["goal"]}/operations/', html)
        self.assertEqual(self.client.get('/goals/emergency').status_code, 200)

    def test_foreign_or_unknown_goal_is_not_found(self):
        with self.app.app_context():
            foreign_tx = FinancialGoalTransaction(
                goal_id=self.ids['foreign_goal'], transaction_type='deposit',
                amount=Decimal('500'), transaction_date=date(2026, 8, 1),
            )
            foreign_fund = EmergencyFundTransaction(
                user_id=self.ids['stranger'], transaction_type='deposit',
                amount=Decimal('500'), transaction_date=date(2026, 8, 1),
            )
            db.session.add_all([foreign_tx, foreign_fund])
            db.session.commit()
            foreign_tx_id, foreign_fund_id = foreign_tx.id, foreign_fund.id
        self.login()
        foreign = self.ids['foreign_goal']
        form = {'transaction_type': 'deposit', 'amount': '1', 'transaction_date': '2026-08-02'}

        self.assertEqual(self.client.get(f'/goals/{foreign}').status_code, 404)
        self.assertEqual(self.client.get('/goals/not-a-goal').status_code, 404)
        self.assertEqual(self.client.post(f'/goals/{foreign}/operations/{foreign_tx_id}/edit', data=form).status_code, 404)
        self.assertEqual(self.client.post(f'/goals/{foreign}/operations/{foreign_tx_id}/delete').status_code, 404)
        self.assertEqual(self.client.post(f'/financial-plan/goals/{foreign}/transactions', data=form).status_code, 404)
        # Another user's fund operation is not reachable through the owner's fund.
        self.assertEqual(self.client.post(f'/goals/emergency/operations/{foreign_fund_id}/delete').status_code, 404)
        self.assertEqual(self.client.post(f'/goals/{self.ids["goal"]}/operations/{foreign_tx_id}/edit', data=form).status_code, 404)
        with self.app.app_context():
            self.assertEqual(db.session.get(FinancialGoalTransaction, foreign_tx_id).amount, Decimal('500.00'))
            self.assertIsNotNone(db.session.get(EmergencyFundTransaction, foreign_fund_id))

    def test_admin_sees_only_own_goals_and_journal_has_no_amounts(self):
        tx_id = self.add('deposit', '98765', date(2026, 8, 1), comment='Секретный комментарий')
        self.login()
        self.client.post(f'/goals/{self.ids["goal"]}/operations/{tx_id}/edit', data={
            'transaction_type': 'deposit', 'amount': '87654', 'transaction_date': '2026-08-01',
        })
        self.login('admin')

        own_goals = _text(self.client.get('/goals').get_data(as_text=True))
        self.assertNotIn('Отпуск-7319', own_goals)
        self.assertEqual(self.client.get(f'/goals/{self.ids["goal"]}').status_code, 404)

        logs = _text(self.client.get('/admin/logs').get_data(as_text=True))
        for secret in ('Отпуск-7319', '98 765', '87 654', '98765', '87654', 'Секретный комментарий'):
            self.assertNotIn(secret, logs)
        with self.app.app_context():
            entry = ActivityLog.query.filter_by(action='Изменил операцию цели').one()
            self.assertEqual(entry.entity_type, 'goal_transaction')
            self.assertEqual(entry.entity_id, tx_id)
            self.assertNotIn('87654', entry.description or '')


class LegacyGoalWithdrawalCategoryTestCase(GoalTestBase):
    def test_new_incomes_cannot_use_goal_withdrawal_but_old_ones_still_work(self):
        self.login()
        page = self.client.get('/incomes').get_data(as_text=True)
        self.assertNotIn('value="goal_withdrawal"', page)

        rejected = self.client.post('/incomes', data={
            'category': 'goal_withdrawal', 'amount': '100', 'income_date': '2026-08-01',
        })
        self.assertIn('Выберите корректную категорию дохода', rejected.get_data(as_text=True))

        with self.app.app_context():
            legacy = Income(
                user_id=self.ids['owner'], amount=Decimal('2500'), category='goal_withdrawal',
                source='Вручную', income_date=date(2026, 8, 3),
            )
            db.session.add(legacy)
            db.session.commit()
            legacy_id = legacy.id

        listing = self.client.get('/incomes?month=2026-08').get_data(as_text=True)
        self.assertIn('Снятие с цели', listing)
        edit_page = self.client.get(f'/incomes/edit/{legacy_id}').get_data(as_text=True)
        self.assertIn('value="goal_withdrawal" selected', edit_page)
        saved = self.client.post(f'/incomes/edit/{legacy_id}', data={
            'category': 'goal_withdrawal', 'amount': '2600', 'income_date': '2026-08-03', 'source': 'Вручную',
        })
        self.assertEqual(saved.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Income, legacy_id).amount, Decimal('2600.00'))


class GoalCsrfTestCase(GoalTestBase):
    csrf = True

    def test_edit_and_delete_require_csrf_and_are_journaled(self):
        tx_id = self.add('deposit', '1000', date(2026, 8, 1))
        self.login()
        goal = self.ids['goal']
        form = {'transaction_type': 'deposit', 'amount': '1500', 'transaction_date': '2026-08-02', 'comment': 'Новое'}

        self.assertEqual(self.client.post(f'/goals/{goal}/operations/{tx_id}/edit', data=form).status_code, 400)
        self.assertEqual(self.client.post(f'/goals/{goal}/operations/{tx_id}/delete').status_code, 400)

        page = self.client.get(f'/goals/{goal}').get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        edited = self.client.post(f'/goals/{goal}/operations/{tx_id}/edit', data={**form, 'csrf_token': token})
        self.assertEqual(edited.status_code, 302)
        with self.app.app_context():
            tx = db.session.get(FinancialGoalTransaction, tx_id)
            self.assertEqual((tx.amount, tx.transaction_date, tx.comment), (Decimal('1500.00'), date(2026, 8, 2), 'Новое'))

        deleted = self.client.post(f'/goals/{goal}/operations/{tx_id}/delete', data={'csrf_token': token})
        self.assertEqual(deleted.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(FinancialGoalTransaction, tx_id))
            actions = [entry.action for entry in ActivityLog.query.order_by(ActivityLog.id).all()]
        self.assertEqual(actions, ['Изменил операцию цели', 'Удалил операцию цели'])


class MonthTotalsConsistencyTestCase(GoalTestBase):
    """One data set, the same numbers on every screen."""

    def setUp(self):
        super().setUp()
        month_start = date.today().replace(day=1)
        with self.app.app_context():
            owner_id = self.ids['owner']
            debt = Debt(
                user_id=owner_id, bank_name='Банк', debt_type='credit_card', product_name='Карта',
                total_amount=Decimal('50000'), remaining_amount=Decimal('40000'), status='active',
            )
            db.session.add(debt)
            db.session.flush()
            db.session.add_all([
                Income(user_id=owner_id, amount=Decimal('100000'), category='salary', source='Работа', income_date=month_start),
                Expense(user_id=owner_id, amount=Decimal('23000'), category='products', title='Продукты', expense_date=month_start),
                # A manual "savings" expense stays a real expense.
                Expense(user_id=owner_id, amount=Decimal('2000'), category='savings', title='Копилка', expense_date=month_start),
                Payment(debt_id=debt.id, amount=Decimal('7000'), payment_date=month_start, remaining_after_payment=Decimal('40000')),
                FinancialPlanPreference(user_id=owner_id),
            ])
            db.session.commit()
        self.add('deposit', '15000', month_start)
        self.add('withdrawal', '4000', month_start)
        self.add('deposit', '6000', month_start, goal='emergency')
        # Operations in another month do not affect this month.
        self.add('deposit', '9000', month_start.replace(year=month_start.year - 1))
        self.login()

    def test_all_screens_show_the_same_month_totals(self):
        incomes, expenses, payments = Decimal('100000'), Decimal('25000'), Decimal('7000')
        transfers = Decimal('17000')  # 15000 − 4000 + 6000
        free = incomes - expenses - payments - transfers

        with self.app.app_context():
            summary = get_finance_summary(self.ids['owner'])
            plan = build_financial_plan(self.ids['owner'], FinancialPlanPreference.query.filter_by(user_id=self.ids['owner']).one())
            bot_text = _build_summary(db.session.get(User, self.ids['owner']))
        self.assertEqual(summary['total_incomes'], incomes)
        self.assertEqual(summary['total_expenses'], expenses)
        self.assertEqual(summary['total_payments'], payments)
        self.assertEqual(summary['goal_transfers']['net'], transfers)
        self.assertEqual(summary['free_balance'], free)
        self.assertEqual(Decimal(str(plan['totals']['income'])), incomes)

        dashboard = _text(self.client.get('/').get_data(as_text=True))
        today = date.today()
        analytics = _text(self.client.get(f'/finance?year={today.year}&month={today.month}').get_data(as_text=True))
        for page in (dashboard, analytics):
            for value in (incomes, expenses, payments, transfers, free):
                self.assertIn(format_currency(value).replace('\xa0', ' '), page)
        self.assertIn('Отложено в цели за месяц', dashboard)
        self.assertIn('Отложено в цели', analytics)
        self.assertIn('Доходы: 100 000 руб.', bot_text)
        self.assertIn('Расходы: 25 000 руб.', bot_text)
        self.assertIn('Отложено в цели: 17 000 руб.', bot_text)
        self.assertIn('Свободный остаток: 51 000 руб.', bot_text)

        goals_page = _text(self.client.get('/goals').get_data(as_text=True))
        self.assertIn('20 000 ₽', goals_page)  # 9000 + 15000 − 4000 on the goal
        self.assertIn('6 000 ₽', goals_page)   # the emergency fund


if __name__ == '__main__':
    unittest.main()
