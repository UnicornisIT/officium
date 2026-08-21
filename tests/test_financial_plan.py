import unittest
from datetime import date
from decimal import Decimal

from app import create_app
from app.models import Debt, EmergencyFundTransaction, Expense, FinancialGoal, FinancialGoalTransaction, FinancialPlanPreference, Income, User
from app.services.financial_plan_service import build_financial_plan
from app.services.finance_summary_service import get_finance_summary
from extensions import db


class FinancialPlanTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'WTF_CSRF_ENABLED': False,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            user = User(
                telegram_id=889,
                username='plan_user',
                first_name='Plan',
                role='user',
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
            self._add_financial_sources()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _add_financial_sources(self):
        db.session.add(Income(
            user_id=self.user_id,
            amount=Decimal('75000.00'),
            category='salary',
            source='Основная работа',
            income_date=date(2026, 8, 5),
        ))
        db.session.add_all([
            Debt(
                user_id=self.user_id,
                bank_name='Сбер',
                debt_type='consumer_credit',
                product_name='Кредит',
                total_amount=Decimal('200000.00'),
                remaining_amount=Decimal('100000.00'),
                minimum_payment=Decimal('16454.00'),
                interest_rate=Decimal('18.00'),
                next_payment_date=date(2026, 9, 5),
                status='active',
            ),
            Debt(
                user_id=self.user_id,
                bank_name='Яндекс',
                debt_type='split',
                product_name='Сплит',
                total_amount=Decimal('12000.00'),
                remaining_amount=Decimal('8000.00'),
                minimum_payment=Decimal('2086.00'),
                next_payment_date=date(2026, 8, 28),
                status='active',
            ),
        ])
        db.session.add_all([
            Expense(
                user_id=self.user_id,
                amount=Decimal('7000.00'),
                category='rent',
                title='Коммунальные услуги',
                expense_date=date(2026, 7, 10),
                is_monthly=True,
                monthly_group_id='utilities',
                generated_for_month='2026-07',
            ),
            Expense(
                user_id=self.user_id,
                amount=Decimal('7000.00'),
                category='rent',
                title='Коммунальные услуги',
                expense_date=date(2026, 8, 10),
                is_monthly=True,
                monthly_group_id='utilities',
                generated_for_month='2026-08',
            ),
            Expense(
                user_id=self.user_id,
                amount=Decimal('20000.00'),
                category='other',
                title='Помощь родственнику',
                expense_date=date(2026, 8, 15),
                is_monthly=True,
                monthly_group_id='family-support',
                generated_for_month='2026-08',
            ),
            Expense(
                user_id=self.user_id,
                amount=Decimal('16454.00'),
                category='loans',
                title='Платеж Сбер кредит',
                expense_date=date(2026, 8, 5),
                is_monthly=True,
                monthly_group_id='duplicate-loan-payment',
                generated_for_month='2026-08',
            ),
        ])
        db.session.add(FinancialPlanPreference(
            user_id=self.user_id,
            living_minimum=Decimal('20000.00'),
            desired_monthly_savings=Decimal('5000.00'),
            emergency_fund_target_amount=Decimal('30000.00'),
            emergency_fund_target_mode='fixed',
            strategy='balanced',
        ))
        db.session.add_all([
            EmergencyFundTransaction(
                user_id=self.user_id,
                transaction_type='deposit',
                amount=Decimal('12000.00'),
                transaction_date=date(2026, 7, 10),
                comment='Первое пополнение',
            ),
            EmergencyFundTransaction(
                user_id=self.user_id,
                transaction_type='withdrawal',
                amount=Decimal('2000.00'),
                transaction_date=date(2026, 7, 15),
                comment='Непредвиденный расход',
            ),
        ])
        db.session.commit()

    def test_builds_plan_from_existing_sources_without_duplicate_monthly_expense(self):
        with self.app.app_context():
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            plan = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))

        self.assertEqual(plan['totals']['income'], 75000.0)
        self.assertEqual(plan['totals']['regular_expenses'], 7000.0)
        self.assertEqual(plan['totals']['family_support'], 20000.0)
        self.assertAlmostEqual(plan['totals']['debt_payments'], 20973.67, places=2)
        self.assertAlmostEqual(plan['totals']['recommended_savings'], 5000.0, places=2)
        self.assertAlmostEqual(plan['totals']['living_budget'], 22026.33, places=2)
        self.assertEqual(plan['totals']['emergency_current'], 10000.0)
        self.assertEqual(plan['emergency_fund']['deposits'], 12000.0)
        self.assertEqual(plan['emergency_fund']['withdrawals'], 2000.0)
        self.assertEqual(len(plan['regular_items']), 1)
        self.assertEqual(len(plan['support_items']), 1)
        self.assertEqual(len(plan['excluded_recurring_items']), 1)

    def test_plan_recalculates_when_source_debt_changes(self):
        with self.app.app_context():
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            before = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))
            debt = Debt.query.filter_by(user_id=self.user_id, debt_type='consumer_credit').one()
            debt.minimum_payment = Decimal('18000.00')
            db.session.commit()
            after = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))

        self.assertGreater(after['totals']['debt_payments'], before['totals']['debt_payments'])
        self.assertLess(after['totals']['living_budget'], before['totals']['living_budget'])

    def test_missing_rate_keeps_distribution_and_adds_explanation(self):
        with self.app.app_context():
            debt = Debt.query.filter_by(user_id=self.user_id, debt_type='consumer_credit').one()
            debt.interest_rate = None
            db.session.commit()
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            plan = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))

        self.assertGreater(plan['totals']['debt_payments'], 0)
        self.assertTrue(any(item['kind'] == 'rate' for item in plan['missing_data']))

    def test_page_saves_only_plan_preferences(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

        response = self.client.post('/financial-plan', data={
            'living_minimum': '25000',
            'desired_monthly_savings': '6000',
            'emergency_fund_target_amount': '50000',
            'emergency_fund_target_mode': 'fixed',
            'strategy': 'safe',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Финансовый план', response.get_data(as_text=True))
        with self.app.app_context():
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            self.assertEqual(preference.living_minimum, Decimal('25000.00'))
            self.assertEqual(preference.strategy, 'safe')

    def test_page_has_separate_goals_section_and_emergency_goal_editor(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

        page_response = self.client.get('/financial-plan')
        page_html = page_response.get_data(as_text=True)
        self.assertEqual(page_response.status_code, 200)
        self.assertIn('id="goals"', page_html)
        self.assertIn('Новая цель', page_html)
        self.assertNotIn('id="emergency-fund"', page_html)

        edit_response = self.client.post('/financial-plan/goals/emergency/edit', data={
            'target_mode': 'three_months',
            'target_amount': '100000',
            'monthly_contribution': '7000',
        })
        self.assertEqual(edit_response.status_code, 302)
        with self.app.app_context():
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            self.assertEqual(preference.emergency_fund_target_mode, 'three_months')
            self.assertEqual(preference.emergency_fund_target_amount, Decimal('100000.00'))
            self.assertEqual(preference.desired_monthly_savings, Decimal('7000.00'))

    def test_plan_uses_selected_month_income_and_ignores_goal_withdrawals(self):
        with self.app.app_context():
            db.session.add_all([
                Income(
                    user_id=self.user_id,
                    amount=Decimal('50000.00'),
                    category='salary',
                    source='Основная работа',
                    income_date=date(2026, 9, 5),
                ),
                Income(
                    user_id=self.user_id,
                    amount=Decimal('9000.00'),
                    category='goal_withdrawal',
                    source='Снятие с цели: Отпуск',
                    income_date=date(2026, 9, 8),
                ),
            ])
            db.session.commit()
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            august = build_financial_plan(self.user_id, preference, today=date(2026, 9, 10), year=2026, month=8)
            september = build_financial_plan(self.user_id, preference, today=date(2026, 9, 10), year=2026, month=9)

        self.assertEqual(august['totals']['income'], 75000.0)
        self.assertEqual(september['totals']['income'], 50000.0)
        self.assertEqual(september['period']['label'], 'сентябрь 2026')

        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True
        page_html = self.client.get('/financial-plan?year=2026&month=9').get_data(as_text=True)
        self.assertIn('План на сентябрь 2026', page_html)
        self.assertIn('value="9" selected', page_html)

    def test_fund_operations_automatically_change_calculated_balance(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

        deposit_response = self.client.post('/financial-plan/emergency-fund', data={
            'transaction_type': 'deposit',
            'amount': '3000',
            'transaction_date': '2026-08-21',
            'comment': 'Перевод в резерв',
        })
        self.assertEqual(deposit_response.status_code, 302)

        withdrawal_response = self.client.post('/financial-plan/emergency-fund', data={
            'transaction_type': 'withdrawal',
            'amount': '1000',
            'transaction_date': '2026-08-21',
            'comment': 'Срочная покупка',
        })
        self.assertEqual(withdrawal_response.status_code, 302)

        with self.app.app_context():
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            plan = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))
            added_deposit = EmergencyFundTransaction.query.filter_by(
                user_id=self.user_id,
                comment='Перевод в резерв',
            ).one()
            added_deposit_id = added_deposit.id
            linked_expense_id = added_deposit.expense_id
            added_withdrawal = EmergencyFundTransaction.query.filter_by(
                user_id=self.user_id,
                comment='Срочная покупка',
            ).one()
            linked_income_id = added_withdrawal.income_id
            linked_expense = db.session.get(Expense, linked_expense_id)
            linked_income = db.session.get(Income, linked_income_id)
            self.assertEqual(linked_expense.category, 'savings')
            self.assertEqual(linked_expense.amount, Decimal('3000.00'))
            self.assertEqual(linked_income.category, 'goal_withdrawal')
            self.assertEqual(linked_income.amount, Decimal('1000.00'))
            report = get_finance_summary(self.user_id, year=2026, month=8)
            self.assertEqual(report['total_expenses'], 46454.0)
            self.assertEqual(report['total_incomes'], 76000.0)
            self.assertTrue(any(item.category == 'savings' for item in report['expenses_this_month']))
            self.assertTrue(any(item.category == 'goal_withdrawal' for item in report['incomes_this_month']))
        self.assertEqual(plan['totals']['emergency_current'], 12000.0)
        self.assertEqual(plan['goals'][0]['monthly_remaining'], 2000.0)

        invalid_response = self.client.post('/financial-plan/emergency-fund', data={
            'transaction_type': 'withdrawal',
            'amount': '50000',
            'transaction_date': '2026-08-21',
            'comment': '',
        }, follow_redirects=True)
        self.assertIn('Нельзя снять больше', invalid_response.get_data(as_text=True))

        delete_response = self.client.post(
            f'/financial-plan/emergency-fund/{added_deposit_id}/delete',
        )
        self.assertEqual(delete_response.status_code, 302)
        with self.app.app_context():
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            plan = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))
            self.assertIsNone(db.session.get(Expense, linked_expense_id))
            self.assertIsNotNone(db.session.get(Income, linked_income_id))
        self.assertEqual(plan['totals']['emergency_current'], 9000.0)

    def test_custom_goal_repeats_fund_operations_and_follows_emergency_priority(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

        create_response = self.client.post('/financial-plan/goals', data={
            'name': 'MacBook',
            'target_amount': '120000',
            'monthly_contribution': '2000',
            'target_date': '2027-08-21',
            'note': 'Рабочий ноутбук',
        })
        self.assertEqual(create_response.status_code, 302)

        with self.app.app_context():
            goal = FinancialGoal.query.filter_by(user_id=self.user_id, name='MacBook').one()
            goal_id = goal.id
            self.assertEqual(goal.priority, 2)
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            plan = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))
            savings_rows = [item for item in plan['allocation'] if item['kind'] == 'savings']
            self.assertEqual([item['label'] for item in savings_rows], ['Финансовая подушка', 'MacBook'])

        deposit_response = self.client.post(f'/financial-plan/goals/{goal_id}/transactions', data={
            'transaction_type': 'deposit',
            'amount': '15000',
            'transaction_date': '2026-08-21',
            'comment': 'Старт',
        })
        self.assertEqual(deposit_response.status_code, 302)

        withdrawal_response = self.client.post(f'/financial-plan/goals/{goal_id}/transactions', data={
            'transaction_type': 'withdrawal',
            'amount': '3000',
            'transaction_date': '2026-08-22',
            'comment': 'Вернул часть',
        })
        self.assertEqual(withdrawal_response.status_code, 302)

        with self.app.app_context():
            preference = FinancialPlanPreference.query.filter_by(user_id=self.user_id).one()
            plan = build_financial_plan(self.user_id, preference, today=date(2026, 8, 21))
            custom_goal = next(item for item in plan['goals'] if item['name'] == 'MacBook')
            self.assertEqual(plan['goals'][0]['name'], 'Финансовая подушка')
            self.assertTrue(plan['goals'][0]['is_system'])
            self.assertEqual(custom_goal['priority'], 2)
            self.assertEqual(custom_goal['balance'], 12000.0)
            self.assertEqual(custom_goal['deposits'], 15000.0)
            self.assertEqual(custom_goal['withdrawals'], 3000.0)
            self.assertEqual(custom_goal['deposited_this_month'], 15000.0)
            self.assertEqual(custom_goal['monthly_remaining'], 0.0)
            savings_rows = [item for item in plan['allocation'] if item['kind'] == 'savings']
            self.assertEqual([item['label'] for item in savings_rows], ['Финансовая подушка'])
            goal_transactions = FinancialGoalTransaction.query.filter_by(goal_id=goal_id).all()
            linked_expense_ids = [item.expense_id for item in goal_transactions if item.expense_id]
            linked_income_ids = [item.income_id for item in goal_transactions if item.income_id]
            self.assertEqual(len(linked_expense_ids), 1)
            self.assertEqual(len(linked_income_ids), 1)

        invalid_response = self.client.post(f'/financial-plan/goals/{goal_id}/transactions', data={
            'transaction_type': 'withdrawal',
            'amount': '50000',
            'transaction_date': '2026-08-23',
        }, follow_redirects=True)
        self.assertIn('Нельзя снять больше', invalid_response.get_data(as_text=True))

        page_html = self.client.get('/financial-plan').get_data(as_text=True)
        self.assertIn('MacBook', page_html)
        self.assertIn(f'/financial-plan/goals/{goal_id}/move', page_html)
        self.assertIn(f'/financial-plan/goals/{goal_id}/edit', page_html)
        self.assertIn('Удалить цель', page_html)

        delete_response = self.client.post(f'/financial-plan/goals/{goal_id}/delete')
        self.assertEqual(delete_response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(FinancialGoal, goal_id))
            self.assertEqual(FinancialGoalTransaction.query.filter_by(goal_id=goal_id).count(), 0)
            self.assertIsNone(db.session.get(Expense, linked_expense_ids[0]))
            self.assertIsNone(db.session.get(Income, linked_income_ids[0]))


if __name__ == '__main__':
    unittest.main()
