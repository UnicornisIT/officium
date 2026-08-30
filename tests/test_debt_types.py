import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Debt, Payment, SplitPurchase, User
from app.services.debt_interest_service import calculate_overdue_interest
from extensions import db


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 7)


class DebtTypesTestCase(unittest.TestCase):
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
                telegram_id=991,
                username='debt_user',
                first_name='Debt',
                role='user',
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user_id)
            session['_fresh'] = True

    def test_can_create_consumer_credit_debt(self):
        self.login()

        response = self.client.post('/api/debts', json={
            'bank_name': 'Сбербанк',
            'debt_type': 'consumer_credit',
            'product_name': 'Потребительский кредит',
            'total_amount': '300000',
            'remaining_amount': '250000',
            'minimum_payment': '15000',
            'first_payment_amount': '2539.73',
            'early_repayment_enabled': True,
            'planned_early_repayment_amount': '20000',
            'interest_rate': '18.5',
            'next_payment_date': '2026-08-15',
            'comment': 'Кредит наличными',
        })

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload['debt']['debt_type'], 'consumer_credit')
        self.assertEqual(payload['debt']['debt_type_label'], 'Потребительский кредит')
        self.assertEqual(payload['debt']['first_payment_amount'], 2539.73)
        self.assertTrue(payload['debt']['is_first_payment_pending'])
        self.assertEqual(payload['debt']['effective_next_payment_amount'], 2539.73)
        self.assertTrue(payload['debt']['early_repayment_enabled'])
        self.assertEqual(payload['debt']['planned_early_repayment_amount'], 20000.0)
        self.assertEqual(payload['debt']['effective_planned_early_repayment_amount'], 5000.0)

        with self.app.app_context():
            debt = Debt.query.filter_by(user_id=self.user_id).one()
            self.assertEqual(debt.debt_type, 'consumer_credit')
            self.assertEqual(debt.first_payment_amount, Decimal('2539.73'))
            self.assertTrue(debt.early_repayment_enabled)
            self.assertEqual(debt.planned_early_repayment_amount, Decimal('20000.00'))

    def test_payment_modal_has_minimum_payment_and_due_date_shortcut(self):
        self.login()

        response = self.client.get('/')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="pm_apply_minimum"', html)
        self.assertIn('onclick="applyMinimumPayment()"', html)
        self.assertIn('id="pm_apply_early"', html)
        self.assertIn('onclick="applyPlannedEarlyRepayment()"', html)
        self.assertIn('id="f_first_payment_amount"', html)
        self.assertLess(html.index('id="f_minimum_payment"'), html.index('id="f_first_payment_amount"'))
        self.assertLess(html.index('id="f_first_payment_amount"'), html.index('id="f_interest_rate"'))
        rate_change_block = html[html.index('class="bank-calc-settings rate-change-settings"'):]
        self.assertIn('Изменение процентной ставки', rate_change_block)
        self.assertIn('id="f_interest_rate_after_change"', rate_change_block)
        self.assertIn('id="f_interest_rate_change_date"', rate_change_block)
        bank_block_start = html.index('<div class="bank-calc-settings">')
        early_block_start = html.index('class="bank-calc-settings early-repayment-settings-block"')
        bank_block = html[bank_block_start:early_block_start]
        early_block = html[early_block_start:html.index('id="f_comment"', early_block_start)]
        self.assertIn('Расчёт банка', bank_block)
        self.assertNotIn('id="f_early_repayment_strategy"', bank_block)
        self.assertIn('Досрочное погашение', early_block)
        self.assertIn('id="f_early_repayment_strategy"', early_block)
        self.assertIn('id="f_early_repayment_enabled"', early_block)
        self.assertIn('id="f_planned_early_repayment_amount"', early_block)
        self.assertIn('id="f_effective_early_repayment_amount"', early_block)
        self.assertIn('Желаемый платёж минус минимальный.', early_block)
        self.assertIn('Подставить сумму и дату ближайшего платежа', html)

    def test_planned_early_repayment_requires_amount_and_only_supports_consumer_credit(self):
        self.login()

        missing_amount = self.client.post('/api/debts', json={
            'bank_name': 'Сбербанк',
            'debt_type': 'consumer_credit',
            'product_name': 'Потребительский кредит',
            'total_amount': '300000',
            'remaining_amount': '250000',
            'minimum_payment': '15000',
            'early_repayment_enabled': True,
        })
        self.assertEqual(missing_amount.status_code, 422)
        self.assertIn('Желаемый платёж', missing_amount.get_json()['error'])

        not_above_minimum = self.client.post('/api/debts', json={
            'bank_name': 'Сбербанк',
            'debt_type': 'consumer_credit',
            'product_name': 'Потребительский кредит',
            'total_amount': '300000',
            'remaining_amount': '250000',
            'minimum_payment': '15000',
            'early_repayment_enabled': True,
            'planned_early_repayment_amount': '15000',
        })
        self.assertEqual(not_above_minimum.status_code, 422)
        self.assertIn('больше минимального платежа', not_above_minimum.get_json()['error'])

        card_response = self.client.post('/api/debts', json={
            'bank_name': 'Банк',
            'debt_type': 'credit_card',
            'product_name': 'Карта',
            'total_amount': '100000',
            'remaining_amount': '50000',
            'minimum_payment': '5000',
            'early_repayment_enabled': True,
            'planned_early_repayment_amount': '10000',
        })
        self.assertEqual(card_response.status_code, 201)
        card = card_response.get_json()['debt']
        self.assertFalse(card['early_repayment_enabled'])
        self.assertIsNone(card['planned_early_repayment_amount'])

    def test_can_create_debt_with_scheduled_interest_rate_change(self):
        self.login()

        response = self.client.post('/api/debts', json={
            'bank_name': 'Сбербанк',
            'debt_type': 'consumer_credit',
            'product_name': 'Потребительский кредит',
            'total_amount': '300000',
            'remaining_amount': '250000',
            'minimum_payment': '15000',
            'interest_rate': '18.5',
            'interest_rate_after_change': '21.9',
            'interest_rate_change_date': '2026-09-01',
            'next_payment_date': '2026-08-15',
        })

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload['debt']['interest_rate_after_change'], 21.9)
        self.assertEqual(payload['debt']['interest_rate_change_date'], '2026-09-01')

        with self.app.app_context():
            debt = Debt.query.filter_by(user_id=self.user_id).one()
            self.assertEqual(debt.interest_rate_for(date(2026, 8, 31)), Decimal('18.50'))
            self.assertEqual(debt.interest_rate_for(date(2026, 9, 1)), Decimal('21.90'))

    def test_payment_schedule_uses_current_debt_terms(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Test Bank',
                debt_type='consumer_credit',
                product_name='Cash Loan',
                total_amount=Decimal('100000.00'),
                remaining_amount=Decimal('100000.00'),
                minimum_payment=Decimal('10000.00'),
                interest_rate=Decimal('12.00'),
                next_payment_date=date(2026, 8, 15),
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        first_row = payload['schedule']['rows'][0]
        self.assertEqual(first_row['payment_date'], '2026-08-15')
        self.assertEqual(first_row['payment'], 10000.0)
        self.assertEqual(first_row['interest'], 1019.18)
        self.assertEqual(first_row['principal'], 8980.82)
        self.assertEqual(first_row['remaining'], 91019.18)
        self.assertEqual(first_row['rate'], 12.0)
        self.assertEqual(first_row['interest_days'], 31)

    def test_payment_schedule_respects_scheduled_interest_rate_change(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Test Bank',
                debt_type='consumer_credit',
                product_name='Cash Loan',
                total_amount=Decimal('20000.00'),
                remaining_amount=Decimal('20000.00'),
                minimum_payment=Decimal('10000.00'),
                interest_rate=Decimal('12.00'),
                interest_rate_after_change=Decimal('24.00'),
                interest_rate_change_date=date(2026, 9, 15),
                next_payment_date=date(2026, 8, 15),
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        rows = response.get_json()['schedule']['rows']
        self.assertEqual(rows[0]['rate'], 12.0)
        self.assertEqual(rows[1]['payment_date'], '2026-09-15')
        self.assertEqual(rows[1]['rate'], 24.0)

    def test_payment_schedule_uses_custom_first_payment_then_monthly_payment(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Сбер',
                debt_type='consumer_credit',
                product_name='Потребительский кредит',
                total_amount=Decimal('500000.00'),
                remaining_amount=Decimal('500000.00'),
                minimum_payment=Decimal('16454.19'),
                first_payment_amount=Decimal('2539.73'),
                interest_rate=Decimal('30.90'),
                next_payment_date=date(2026, 8, 28),
                interest_period_start_date=date(2026, 8, 22),
                day_count_convention='fixed_365',
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        rows = response.get_json()['schedule']['rows']
        self.assertEqual(rows[0]['payment'], 2539.73)
        self.assertEqual(rows[0]['interest'], 2539.73)
        self.assertEqual(rows[0]['principal'], 0.0)
        self.assertEqual(rows[0]['remaining'], 500000.0)
        self.assertEqual(rows[1]['payment'], 16454.19)

    def test_recording_custom_first_payment_advances_to_regular_payment(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Сбер',
                debt_type='consumer_credit',
                product_name='Потребительский кредит',
                total_amount=Decimal('500000.00'),
                remaining_amount=Decimal('500000.00'),
                minimum_payment=Decimal('16454.19'),
                first_payment_amount=Decimal('2539.73'),
                interest_rate=Decimal('30.90'),
                next_payment_date=date(2026, 8, 28),
                is_payment_recurring=True,
                interest_period_start_date=date(2026, 8, 22),
                day_count_convention='fixed_365',
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '2539.73',
            'payment_date': '2026-08-28',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['next_payment_date_advanced'])
        self.assertEqual(payload['payment']['interest_amount'], 2539.73)
        self.assertEqual(payload['payment']['principal_amount'], 0.0)
        self.assertEqual(payload['debt']['remaining_amount'], 500000.0)
        self.assertEqual(payload['debt']['next_payment_date'], '2026-09-28')
        self.assertFalse(payload['debt']['is_first_payment_pending'])
        self.assertEqual(payload['debt']['effective_next_payment_amount'], 16454.19)

    def test_combined_payment_records_required_and_early_parts_as_one_operation(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Сбер',
                debt_type='consumer_credit',
                product_name='Потребительский кредит',
                total_amount=Decimal('500000.00'),
                remaining_amount=Decimal('500000.00'),
                minimum_payment=Decimal('16454.19'),
                first_payment_amount=Decimal('2539.73'),
                interest_rate=Decimal('30.90'),
                next_payment_date=date(2026, 8, 28),
                is_payment_recurring=True,
                interest_period_start_date=date(2026, 8, 22),
                day_count_convention='fixed_365',
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '25000.00',
            'scheduled_payment_amount': '2539.73',
            'payment_date': '2026-08-28',
            'is_early_repayment': True,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['payment']['is_early_repayment'])
        self.assertEqual(payload['payment']['scheduled_payment_amount'], 2539.73)
        self.assertEqual(payload['payment']['early_repayment_amount'], 22460.27)
        self.assertEqual(payload['payment']['interest_amount'], 2539.73)
        self.assertEqual(payload['payment']['principal_amount'], 22460.27)
        self.assertTrue(payload['next_payment_date_advanced'])
        self.assertEqual(payload['debt']['next_payment_date'], '2026-09-28')
        self.assertFalse(payload['debt']['is_first_payment_pending'])
        self.assertEqual(payload['debt']['remaining_amount'], 477539.73)

    def test_can_save_bank_calculation_settings(self):
        self.login()

        response = self.client.post('/api/debts', json={
            'bank_name': 'Сбер',
            'debt_type': 'consumer_credit',
            'product_name': 'Потребительский кредит',
            'total_amount': '300000',
            'remaining_amount': '192990.50',
            'minimum_payment': '8355.71',
            'interest_rate': '32.75',
            'next_payment_date': '2026-08-21',
            'repayment_type': 'annuity',
            'day_count_convention': 'fixed_365',
            'include_payment_day': True,
            'interest_period_start_date': '2026-07-21',
            'early_repayment_strategy': 'reduce_payment',
            'loan_term_months': '48',
            'monthly_fee_amount': '250',
            'bank_remaining_amount': '192990.50',
        })

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()['debt']
        self.assertEqual(payload['day_count_convention'], 'fixed_365')
        self.assertTrue(payload['include_payment_day'])
        self.assertEqual(payload['early_repayment_strategy'], 'reduce_payment')
        self.assertEqual(payload['loan_term_months'], 48)
        self.assertEqual(payload['monthly_fee_amount'], 250.0)
        self.assertEqual(payload['bank_remaining_delta'], 0.0)

    def test_schedule_includes_payment_day_and_monthly_fee(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Test Bank',
                debt_type='consumer_credit',
                product_name='Cash Loan',
                total_amount=Decimal('100000.00'),
                remaining_amount=Decimal('100000.00'),
                minimum_payment=Decimal('10000.00'),
                interest_rate=Decimal('12.00'),
                next_payment_date=date(2026, 8, 15),
                day_count_convention='fixed_365',
                include_payment_day=True,
                monthly_fee_amount=Decimal('250.00'),
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        first_row = response.get_json()['schedule']['rows'][0]
        self.assertEqual(first_row['interest_days'], 32)
        self.assertEqual(first_row['fee'], 250.0)
        self.assertEqual(first_row['payment'], 10250.0)

    def test_split_schedule_uses_common_two_dates_per_month(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Яндекс Пэй',
                debt_type='split',
                product_name='Яндекс Сплит',
                total_amount=Decimal('10000.00'),
                remaining_amount=Decimal('10000.00'),
                next_payment_date=date(2026, 8, 12),
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        schedule = response.get_json()['schedule']
        rows = schedule['rows']
        self.assertEqual(schedule['kind'], 'split')
        self.assertEqual(schedule['interval_label'], 'раз в 2 недели')
        self.assertEqual(schedule['total_interest'], 0.0)
        self.assertEqual(schedule['total_fees'], 0.0)
        self.assertEqual([row['payment_date'] for row in rows], [
            '2026-08-12',
            '2026-08-27',
            '2026-09-12',
            '2026-09-27',
        ])
        self.assertEqual([row['payment'] for row in rows], [2500.0, 2500.0, 2500.0, 2500.0])
        self.assertEqual(rows[-1]['remaining'], 0.0)

    def test_split_schedule_uses_known_next_payment_amount(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Яндекс Пэй',
                debt_type='split',
                product_name='Яндекс Сплит',
                total_amount=Decimal('99900.00'),
                remaining_amount=Decimal('6010.00'),
                minimum_payment=Decimal('2004.00'),
                next_payment_date=date(2026, 8, 12),
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        rows = response.get_json()['schedule']['rows']
        self.assertEqual([row['payment_date'] for row in rows], [
            '2026-08-12',
            '2026-08-27',
            '2026-09-12',
        ])
        self.assertEqual([row['payment'] for row in rows], [2004.0, 2004.0, 2002.0])
        self.assertEqual(rows[-1]['remaining'], 0.0)

    def test_split_schedule_marks_existing_payments_as_paid(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Яндекс Пэй',
                debt_type='split',
                product_name='Яндекс Сплит',
                total_amount=Decimal('99900.00'),
                remaining_amount=Decimal('6010.00'),
                minimum_payment=None,
                next_payment_date=date(2026, 7, 20),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            db.session.add_all([
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 7, 20),
                    remaining_after_payment=Decimal('10018.00'),
                ),
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 7, 27),
                    remaining_after_payment=Decimal('8014.00'),
                ),
            ])
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        schedule = response.get_json()['schedule']
        rows = schedule['rows']
        self.assertEqual(schedule['total_paid'], 4008.0)
        self.assertEqual(schedule['total_planned'], 6010.0)
        self.assertEqual(rows[0]['payment_date'], '2026-07-20')
        self.assertEqual(rows[0]['status'], 'paid')
        self.assertEqual(rows[1]['payment_date'], '2026-07-27')
        self.assertEqual(rows[1]['status'], 'paid')
        self.assertEqual([row['payment_date'] for row in rows[2:]], [
            '2026-08-12',
            '2026-08-27',
            '2026-09-12',
        ])
        self.assertEqual([row['payment'] for row in rows[2:]], [2004.0, 2004.0, 2002.0])
        self.assertEqual(rows[-1]['remaining'], 0.0)

        with self.app.app_context():
            debt = db.session.get(Debt, debt_id)
            self.assertEqual(debt.effective_next_payment_date(), date(2026, 8, 12))
            self.assertEqual(debt.to_dict()['effective_next_payment_date'], '2026-08-12')

    def test_split_schedule_treats_future_payment_records_as_planned(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Yandex Pay',
                debt_type='split',
                product_name='Yandex Split',
                total_amount=Decimal('101638.00'),
                remaining_amount=Decimal('3744.00'),
                minimum_payment=Decimal('2004.00'),
                next_payment_date=date(2026, 7, 20),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            db.session.add_all([
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 7, 20),
                    remaining_after_payment=Decimal('9756.00'),
                ),
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 7, 27),
                    remaining_after_payment=Decimal('7752.00'),
                ),
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 8, 12),
                    remaining_after_payment=Decimal('5748.00'),
                ),
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 8, 27),
                    remaining_after_payment=Decimal('3744.00'),
                ),
                SplitPurchase(
                    debt_id=debt.id,
                    title='Charger',
                    amount=Decimal('1738.00'),
                    purchase_date=date(2026, 8, 6),
                    installments_count=4,
                ),
            ])
            db.session.commit()
            debt_id = debt.id

        with patch('app.services.debt_schedule_service.date', FrozenDate), patch('app.models.date', FrozenDate):
            response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        schedule = response.get_json()['schedule']
        rows = schedule['rows']
        self.assertEqual(schedule['total_paid'], 4008.0)
        self.assertEqual(schedule['total_planned'], 7752.0)
        self.assertEqual([row['status'] for row in rows], [
            'paid',
            'paid',
            'planned',
            'planned',
            'planned',
            'planned',
            'planned',
        ])
        self.assertEqual([row['payment_date'] for row in rows], [
            '2026-07-20',
            '2026-07-27',
            '2026-08-12',
            '2026-08-27',
            '2026-09-12',
            '2026-09-27',
            '2026-10-12',
        ])
        self.assertEqual([row['payment'] for row in rows[2:]], [
            2004.0,
            2439.0,
            2441.0,
            435.0,
            433.0,
        ])
        row_by_date = {row['payment_date']: row for row in rows}
        self.assertEqual(
            [component['amount'] for component in row_by_date['2026-08-27']['components']],
            [2004.0, 435.0],
        )
        self.assertEqual(
            [component['amount'] for component in row_by_date['2026-09-12']['components']],
            [2006.0, 435.0],
        )
        self.assertEqual(rows[-1]['remaining'], 0.0)

        with self.app.app_context(), patch('app.models.date', FrozenDate):
            debt = db.session.get(Debt, debt_id)
            self.assertEqual(debt.effective_next_payment_date(), date(2026, 8, 12))

    def test_split_purchase_from_august_six_matches_yandex_payment_dates(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Yandex Pay',
                debt_type='split',
                product_name='Yandex Split',
                total_amount=Decimal('11786.00'),
                remaining_amount=Decimal('7748.00'),
                minimum_payment=Decimal('2004.00'),
                next_payment_date=date(2026, 8, 12),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            db.session.add(
                SplitPurchase(
                    debt_id=debt.id,
                    title='Charger',
                    amount=Decimal('1738.00'),
                    purchase_date=date(2026, 8, 6),
                    installments_count=4,
                )
            )
            db.session.commit()
            debt_id = debt.id

        with patch('app.services.debt_schedule_service.date', FrozenDate), patch('app.models.date', FrozenDate):
            response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        rows = response.get_json()['schedule']['rows']
        self.assertEqual([row['payment_date'] for row in rows], [
            '2026-08-12',
            '2026-08-27',
            '2026-09-12',
            '2026-09-27',
            '2026-10-12',
        ])
        self.assertEqual([row['payment'] for row in rows], [
            2004.0,
            2439.0,
            2437.0,
            435.0,
            433.0,
        ])

    def test_split_new_purchase_joins_existing_common_payment_date(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Яндекс Пэй',
                debt_type='split',
                product_name='Яндекс Сплит',
                total_amount=Decimal('10018.00'),
                remaining_amount=Decimal('14010.00'),
                next_payment_date=date(2026, 7, 20),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            db.session.add_all([
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 7, 20),
                    remaining_after_payment=Decimal('8014.00'),
                ),
                Payment(
                    debt_id=debt.id,
                    amount=Decimal('2004.00'),
                    principal_amount=Decimal('2004.00'),
                    interest_amount=Decimal('0.00'),
                    payment_date=date(2026, 7, 27),
                    remaining_after_payment=Decimal('6010.00'),
                ),
                SplitPurchase(
                    debt_id=debt.id,
                    title='Покупка 28 августа',
                    amount=Decimal('8000.00'),
                    purchase_date=date(2026, 8, 28),
                    installments_count=4,
                ),
            ])
            db.session.commit()
            debt_id = debt.id

        response = self.client.get(f'/api/debts/{debt_id}/schedule')

        self.assertEqual(response.status_code, 200)
        rows = response.get_json()['schedule']['rows']
        row_by_date = {row['payment_date']: row for row in rows}
        self.assertEqual(row_by_date['2026-09-12']['payment'], 4002.0)
        self.assertEqual(
            [component['amount'] for component in row_by_date['2026-09-12']['components']],
            [2002.0, 2000.0],
        )
        self.assertEqual(row_by_date['2026-09-27']['payment'], 2000.0)
        self.assertEqual(row_by_date['2026-10-12']['payment'], 2000.0)
        self.assertEqual(row_by_date['2026-10-27']['payment'], 2000.0)

    def test_can_add_purchase_to_common_split_schedule(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Яндекс Пэй',
                debt_type='split',
                product_name='Яндекс Сплит',
                total_amount=Decimal('10018.00'),
                remaining_amount=Decimal('6010.00'),
                minimum_payment=Decimal('2004.00'),
                next_payment_date=date(2026, 8, 12),
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/split-purchases', json={
            'title': 'Новая покупка',
            'amount': '8000',
            'purchase_date': '2026-08-28',
            'installments_count': '4',
        })

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload['debt']['remaining_amount'], 14010.0)
        self.assertEqual(payload['debt']['total_amount'], 18018.0)
        self.assertEqual(payload['purchase']['purchase_date'], '2026-08-28')
        row_by_date = {row['payment_date']: row for row in payload['schedule']['rows']}
        self.assertEqual(row_by_date['2026-09-12']['payment'], 4002.0)

        with self.app.app_context():
            self.assertEqual(SplitPurchase.query.filter_by(debt_id=debt_id).count(), 1)

    def test_interest_rate_change_requires_rate_and_date_together(self):
        self.login()

        response = self.client.post('/api/debts', json={
            'bank_name': 'Сбербанк',
            'debt_type': 'consumer_credit',
            'product_name': 'Потребительский кредит',
            'total_amount': '300000',
            'remaining_amount': '250000',
            'interest_rate': '18.5',
            'interest_rate_after_change': '21.9',
        })

        self.assertEqual(response.status_code, 422)
        self.assertIn('дату смены ставки', response.get_json()['error'])

    def test_overdue_interest_is_split_by_scheduled_rate_change(self):
        debt = Debt(
            user_id=self.user_id,
            bank_name='Сбербанк',
            debt_type='consumer_credit',
            product_name='Потребительский кредит',
            total_amount=Decimal('100000.00'),
            remaining_amount=Decimal('100000.00'),
            interest_rate=Decimal('10.00'),
            interest_rate_after_change=Decimal('20.00'),
            interest_rate_change_date=date(2026, 7, 15),
            next_payment_date=date(2026, 7, 10),
            status='active',
        )

        summary = calculate_overdue_interest(debt, today=date(2026, 7, 20))

        self.assertEqual(len(summary['periods']), 2)
        self.assertEqual(summary['periods'][0]['days'], 5)
        self.assertEqual(summary['periods'][0]['rate'], 10.0)
        self.assertEqual(summary['periods'][1]['days'], 5)
        self.assertEqual(summary['periods'][1]['rate'], 20.0)
        self.assertEqual(summary['total_overdue_interest'], 410.96)

    def test_recurring_payment_date_advances_after_required_payment(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Сбербанк',
                debt_type='consumer_credit',
                product_name='Потребительский кредит',
                total_amount=Decimal('300000.00'),
                remaining_amount=Decimal('300000.00'),
                minimum_payment=Decimal('15000.00'),
                interest_rate=Decimal('18.5'),
                next_payment_date=date(2026, 8, 15),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '15000',
            'payment_date': '2026-08-10',
            'comment': 'Обязательный платеж',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['next_payment_date_advanced'])
        self.assertEqual(payload['debt']['next_payment_date'], '2026-09-15')

        with self.app.app_context():
            debt = db.session.get(Debt, debt_id)
            self.assertEqual(debt.next_payment_date, date(2026, 9, 15))
            self.assertEqual(debt.remaining_amount, Decimal('285000.00'))

    def test_on_time_required_payment_has_no_auto_interest(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Совкомбанк',
                debt_type='credit_card',
                product_name='Халва',
                total_amount=Decimal('20000.00'),
                remaining_amount=Decimal('17140.54'),
                minimum_payment=Decimal('7173.66'),
                interest_rate=Decimal('36.00'),
                next_payment_date=date(2026, 7, 26),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '7173.66',
            'payment_date': '2026-07-26',
            'comment': 'В срок',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['payment']['interest_amount'], 0.0)
        self.assertEqual(payload['payment']['principal_amount'], 7173.66)
        self.assertEqual(payload['payment']['remaining_after_payment'], 9966.88)

    def test_editing_previous_due_payment_does_not_advance_next_due_date(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Sovcombank',
                debt_type='credit_card',
                product_name='Halva',
                total_amount=Decimal('20000.00'),
                remaining_amount=Decimal('9966.88'),
                minimum_payment=Decimal('7173.66'),
                interest_rate=Decimal('36.00'),
                next_payment_date=date(2026, 8, 26),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            payment = Payment(
                debt_id=debt.id,
                amount=Decimal('7173.66'),
                principal_amount=Decimal('6975.78'),
                interest_amount=Decimal('197.88'),
                fee_amount=Decimal('0.00'),
                payment_date=date(2026, 7, 26),
                remaining_after_payment=Decimal('9966.88'),
            )
            db.session.add(payment)
            db.session.commit()
            debt_id = debt.id
            payment_id = payment.id

        response = self.client.put(f'/api/debts/{debt_id}/payments/{payment_id}', json={
            'amount': '7173.66',
            'payment_date': '2026-07-26',
            'principal_amount': '7173.66',
            'interest_amount': '0',
            'fee_amount': '0',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['debt']['next_payment_date'], '2026-08-26')
        self.assertEqual(payload['payment']['interest_amount'], 0.0)
        self.assertEqual(payload['payment']['principal_amount'], 7173.66)
        self.assertEqual(payload['payment']['remaining_after_payment'], 9769.0)

    def test_late_required_payment_gets_auto_overdue_interest(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Совкомбанк',
                debt_type='credit_card',
                product_name='Халва',
                total_amount=Decimal('20000.00'),
                remaining_amount=Decimal('17140.54'),
                minimum_payment=Decimal('7173.66'),
                interest_rate=Decimal('36.00'),
                next_payment_date=date(2026, 7, 26),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '7173.66',
            'payment_date': '2026-07-27',
            'comment': 'На день позже',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertGreater(payload['payment']['interest_amount'], 0)
        self.assertLess(payload['payment']['principal_amount'], 7173.66)

    def test_recurring_payment_date_stays_when_payment_is_below_minimum(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Сбербанк',
                debt_type='consumer_credit',
                product_name='Потребительский кредит',
                total_amount=Decimal('300000.00'),
                remaining_amount=Decimal('300000.00'),
                minimum_payment=Decimal('15000.00'),
                interest_rate=Decimal('18.5'),
                next_payment_date=date(2026, 8, 15),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '5000',
            'payment_date': '2026-08-10',
            'comment': 'Частичный платеж',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['next_payment_date_advanced'])
        self.assertEqual(payload['debt']['next_payment_date'], '2026-08-15')

        with self.app.app_context():
            debt = db.session.get(Debt, debt_id)
            self.assertEqual(debt.next_payment_date, date(2026, 8, 15))

    def test_payment_with_bank_breakdown_reduces_only_principal(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Сбер',
                debt_type='consumer_credit',
                product_name='Потребительский кредит',
                total_amount=Decimal('300000.00'),
                remaining_amount=Decimal('192990.50'),
                minimum_payment=Decimal('8355.71'),
                interest_rate=Decimal('32.75'),
                next_payment_date=date(2026, 8, 21),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '8355.71',
            'principal_amount': '3025.92',
            'interest_amount': '5329.79',
            'payment_date': '2026-08-21',
            'comment': 'По графику банка',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['payment']['principal_amount'], 3025.92)
        self.assertEqual(payload['payment']['interest_amount'], 5329.79)
        self.assertEqual(payload['payment']['remaining_after_payment'], 189964.58)
        self.assertEqual(payload['debt']['remaining_amount'], 189964.58)

    def test_early_repayment_is_saved_and_does_not_advance_required_payment_date(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Test Bank',
                debt_type='consumer_credit',
                product_name='Cash Loan',
                total_amount=Decimal('300000.00'),
                remaining_amount=Decimal('300000.00'),
                minimum_payment=Decimal('15000.00'),
                interest_rate=Decimal('18.5'),
                next_payment_date=date(2026, 8, 15),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '20000',
            'payment_date': '2026-08-10',
            'comment': 'Extra principal',
            'is_early_repayment': True,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['payment']['is_early_repayment'])
        self.assertFalse(payload['next_payment_date_advanced'])
        self.assertEqual(payload['debt']['next_payment_date'], '2026-08-15')

        history_response = self.client.get(f'/api/debts/{debt_id}/payments')
        history = history_response.get_json()['payments']
        self.assertTrue(history[0]['is_early_repayment'])

        with self.app.app_context():
            debt = db.session.get(Debt, debt_id)
            self.assertEqual(debt.next_payment_date, date(2026, 8, 15))
            self.assertEqual(debt.remaining_amount, Decimal('280000.00'))

    def test_split_payment_advances_recurring_date_by_two_weeks(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Яндекс Пэй',
                debt_type='split',
                product_name='Яндекс Сплит',
                total_amount=Decimal('10000.00'),
                remaining_amount=Decimal('10000.00'),
                minimum_payment=Decimal('2500.00'),
                interest_rate=Decimal('0.00'),
                next_payment_date=date(2026, 8, 12),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.commit()
            debt_id = debt.id

        response = self.client.post(f'/api/debts/{debt_id}/payments', json={
            'amount': '2500',
            'payment_date': '2026-08-12',
            'comment': 'Часть сплита',
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['next_payment_date_advanced'])
        self.assertEqual(payload['debt']['next_payment_date'], '2026-08-27')
        self.assertEqual(payload['payment']['principal_amount'], 2500.0)
        self.assertEqual(payload['payment']['interest_amount'], 0.0)

        with self.app.app_context():
            debt = db.session.get(Debt, debt_id)
            self.assertEqual(debt.remaining_amount, Decimal('7500.00'))
            self.assertEqual(debt.next_payment_date, date(2026, 8, 27))

    def test_can_edit_payment_and_mark_it_as_early_repayment(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Test Bank',
                debt_type='consumer_credit',
                product_name='Cash Loan',
                total_amount=Decimal('300000.00'),
                remaining_amount=Decimal('189828.07'),
                minimum_payment=Decimal('8355.71'),
                interest_rate=Decimal('32.75'),
                next_payment_date=date(2026, 8, 21),
                is_payment_recurring=True,
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            early_candidate = Payment(
                debt_id=debt.id,
                amount=Decimal('10000.00'),
                principal_amount=Decimal('10000.00'),
                interest_amount=Decimal('0.00'),
                payment_date=date(2026, 7, 20),
                comment='Досрочное',
                is_early_repayment=False,
                remaining_after_payment=Decimal('190000.00'),
            )
            small_payment = Payment(
                debt_id=debt.id,
                amount=Decimal('171.93'),
                principal_amount=Decimal('171.93'),
                interest_amount=Decimal('0.00'),
                payment_date=date(2026, 7, 21),
                comment=None,
                is_early_repayment=False,
                remaining_after_payment=Decimal('189828.07'),
            )
            db.session.add_all([early_candidate, small_payment])
            db.session.commit()
            debt_id = debt.id
            payment_id = early_candidate.id

        response = self.client.put(f'/api/debts/{debt_id}/payments/{payment_id}', json={
            'amount': '10000',
            'payment_date': '2026-07-20',
            'comment': 'Досрочное',
            'is_early_repayment': True,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['payment']['is_early_repayment'])
        self.assertEqual(payload['debt']['next_payment_date'], '2026-07-21')

        with self.app.app_context():
            debt = db.session.get(Debt, debt_id)
            edited_payment = db.session.get(Payment, payment_id)
            self.assertTrue(edited_payment.is_early_repayment)
            self.assertEqual(debt.next_payment_date, date(2026, 7, 21))
            self.assertEqual(debt.remaining_amount, Decimal('189828.07'))

    def test_editing_payment_amount_recalculates_following_balances(self):
        self.login()
        with self.app.app_context():
            debt = Debt(
                user_id=self.user_id,
                bank_name='Test Bank',
                debt_type='consumer_credit',
                product_name='Cash Loan',
                total_amount=Decimal('100000.00'),
                remaining_amount=Decimal('85000.00'),
                minimum_payment=Decimal('10000.00'),
                interest_rate=Decimal('12.00'),
                next_payment_date=date(2026, 8, 15),
                status='active',
            )
            db.session.add(debt)
            db.session.flush()
            first_payment = Payment(
                debt_id=debt.id,
                amount=Decimal('10000.00'),
                principal_amount=Decimal('10000.00'),
                interest_amount=Decimal('0.00'),
                payment_date=date(2026, 7, 1),
                remaining_after_payment=Decimal('90000.00'),
            )
            second_payment = Payment(
                debt_id=debt.id,
                amount=Decimal('5000.00'),
                principal_amount=Decimal('5000.00'),
                interest_amount=Decimal('0.00'),
                payment_date=date(2026, 7, 10),
                remaining_after_payment=Decimal('85000.00'),
            )
            db.session.add_all([first_payment, second_payment])
            db.session.commit()
            debt_id = debt.id
            first_payment_id = first_payment.id
            second_payment_id = second_payment.id

        response = self.client.put(f'/api/debts/{debt_id}/payments/{first_payment_id}', json={
            'amount': '12000',
            'principal_amount': '12000',
            'interest_amount': '0',
            'payment_date': '2026-07-01',
            'comment': '',
            'is_early_repayment': False,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['debt']['remaining_amount'], 83000.0)

        with self.app.app_context():
            debt = db.session.get(Debt, debt_id)
            first_payment = db.session.get(Payment, first_payment_id)
            second_payment = db.session.get(Payment, second_payment_id)
            self.assertEqual(first_payment.remaining_after_payment, Decimal('88000.00'))
            self.assertEqual(second_payment.remaining_after_payment, Decimal('83000.00'))
            self.assertEqual(debt.remaining_amount, Decimal('83000.00'))


if __name__ == '__main__':
    unittest.main()
