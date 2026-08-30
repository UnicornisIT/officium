import unittest
from decimal import Decimal

from app.models import ActivityLog, Debt, EmergencyFundTransaction, Expense, FinancialGoal, FinancialGoalTransaction, FinancialPlanPreference, Income, Payment, SplitPurchase, TelegramConversationState, TelegramProcessedUpdate


class SchemaContractTestCase(unittest.TestCase):
    def test_debt_type_enum_contains_supported_values(self):
        debt_type = Debt.__table__.c.debt_type.type

        self.assertEqual(
            tuple(debt_type.enums),
            ('credit_card', 'consumer_credit', 'split', 'mortgage'),
        )

    def test_debt_type_label_supports_mortgage(self):
        debt = Debt(
            bank_name='Test Bank',
            debt_type='mortgage',
            product_name='Home Loan',
            total_amount=Decimal('100.00'),
            remaining_amount=Decimal('50.00'),
            user_id=1,
        )

        self.assertEqual(debt.to_dict()['debt_type_label'], 'Ипотека')

    def test_debt_type_label_supports_consumer_credit(self):
        debt = Debt(
            bank_name='Test Bank',
            debt_type='consumer_credit',
            product_name='Cash Loan',
            total_amount=Decimal('100.00'),
            remaining_amount=Decimal('50.00'),
            user_id=1,
        )

        self.assertEqual(debt.to_dict()['debt_type_label'], 'Потребительский кредит')

    def test_debt_contains_recurring_payment_date_flag(self):
        columns = Debt.__table__.c

        self.assertIn('is_payment_recurring', columns)
        self.assertFalse(columns.is_payment_recurring.nullable)

    def test_debt_contains_scheduled_interest_rate_change_fields(self):
        columns = Debt.__table__.c

        self.assertIn('interest_rate_after_change', columns)
        self.assertIn('interest_rate_change_date', columns)

    def test_debt_contains_bank_calculation_settings(self):
        columns = Debt.__table__.c

        for column_name in (
            'repayment_type',
            'day_count_convention',
            'include_payment_day',
            'interest_period_start_date',
            'first_payment_amount',
            'early_repayment_strategy',
            'loan_term_months',
            'monthly_fee_amount',
            'bank_remaining_amount',
        ):
            self.assertIn(column_name, columns)

    def test_debt_contains_planned_early_repayment_settings(self):
        columns = Debt.__table__.c

        self.assertIn('early_repayment_enabled', columns)
        self.assertIn('planned_early_repayment_amount', columns)
        self.assertFalse(columns.early_repayment_enabled.nullable)

    def test_payment_contains_early_repayment_flag(self):
        columns = Payment.__table__.c

        self.assertIn('is_early_repayment', columns)
        self.assertIn('scheduled_payment_amount', columns)
        self.assertFalse(columns.is_early_repayment.nullable)

    def test_payment_contains_bank_like_breakdown_fields(self):
        columns = Payment.__table__.c

        self.assertIn('principal_amount', columns)
        self.assertIn('interest_amount', columns)
        self.assertIn('fee_amount', columns)
        self.assertIn('bank_remaining_after_payment', columns)
        self.assertFalse(columns.principal_amount.nullable)
        self.assertFalse(columns.interest_amount.nullable)
        self.assertFalse(columns.fee_amount.nullable)

    def test_split_purchase_schema_tracks_purchases_inside_common_split(self):
        columns = SplitPurchase.__table__.c

        for column_name in (
            'debt_id',
            'title',
            'amount',
            'purchase_date',
            'installments_count',
        ):
            self.assertIn(column_name, columns)
        self.assertFalse(columns.amount.nullable)
        self.assertFalse(columns.purchase_date.nullable)
        self.assertFalse(columns.installments_count.nullable)

    def test_expense_category_enum_contains_restaurants(self):
        expense_category = Expense.__table__.c.category.type

        self.assertEqual(
            tuple(expense_category.enums),
            (
                'products', 'transport', 'communication', 'rent', 'loans',
                'restaurants', 'entertainment', 'health', 'education',
                'clothing', 'subscriptions', 'savings', 'other',
            ),
        )

    def test_income_category_enum_contains_vacation_pay(self):
        income_category = Income.__table__.c.category.type

        self.assertEqual(
            tuple(income_category.enums),
            (
                'salary', 'advance', 'side_job', 'debt_return', 'bonus',
                'scholarship', 'vacation_pay', 'goal_withdrawal', 'other',
            ),
        )

    def test_financial_plan_preferences_store_only_user_choices(self):
        columns = FinancialPlanPreference.__table__.c

        self.assertEqual(
            set(columns.keys()),
            {
                'id', 'user_id', 'living_minimum', 'desired_monthly_savings',
                'emergency_fund_target_amount',
                'emergency_fund_target_mode', 'strategy', 'created_at', 'updated_at',
            },
        )
        self.assertTrue(columns.user_id.unique)

    def test_emergency_fund_uses_transactions_as_balance_source(self):
        columns = EmergencyFundTransaction.__table__.c

        self.assertEqual(
            set(columns.keys()),
            {
                'id', 'user_id', 'transaction_type', 'amount',
                'transaction_date', 'comment', 'expense_id', 'income_id', 'created_at',
            },
        )
        self.assertEqual(tuple(columns.transaction_type.type.enums), ('deposit', 'withdrawal'))
        self.assertFalse(columns.amount.nullable)

    def test_custom_goals_store_plan_and_use_transaction_history(self):
        goal_columns = FinancialGoal.__table__.c
        transaction_columns = FinancialGoalTransaction.__table__.c

        self.assertEqual(
            set(goal_columns.keys()),
            {
                'id', 'user_id', 'name', 'target_amount', 'monthly_contribution',
                'target_date', 'note', 'priority', 'created_at', 'updated_at',
            },
        )
        self.assertEqual(
            set(transaction_columns.keys()),
            {
                'id', 'goal_id', 'transaction_type', 'amount',
                'transaction_date', 'comment', 'expense_id', 'income_id', 'created_at',
            },
        )
        self.assertEqual(tuple(transaction_columns.transaction_type.type.enums), ('deposit', 'withdrawal'))

    def test_activity_log_contains_request_context_columns(self):
        columns = ActivityLog.__table__.c

        self.assertIn('ip_address', columns)
        self.assertEqual(columns.ip_address.type.length, 100)
        self.assertIn('user_agent', columns)

    def test_telegram_processed_update_stores_only_update_id_and_timestamp(self):
        columns = TelegramProcessedUpdate.__table__.c

        self.assertEqual(set(columns.keys()), {'update_id', 'created_at'})
        self.assertTrue(columns.update_id.primary_key)

    def test_telegram_conversation_state_has_ttl_and_single_active_state(self):
        columns = TelegramConversationState.__table__.c

        self.assertIn('telegram_id', columns)
        self.assertIn('flow', columns)
        self.assertIn('step', columns)
        self.assertIn('data', columns)
        self.assertIn('expires_at', columns)
        self.assertFalse(columns.expires_at.nullable)
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in TelegramConversationState.__table__.constraints
            if constraint.__class__.__name__ == 'UniqueConstraint'
        }
        index_names = {index.name for index in TelegramConversationState.__table__.indexes}
        self.assertIn(('telegram_id',), unique_columns)
        self.assertIn('ix_telegram_conversation_states_telegram_id', index_names)
        self.assertIn('ix_telegram_conversation_states_expires_at', index_names)


if __name__ == '__main__':
    unittest.main()
