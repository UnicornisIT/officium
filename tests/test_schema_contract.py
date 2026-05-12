import unittest
from decimal import Decimal

from app.models import ActivityLog, Debt


class SchemaContractTestCase(unittest.TestCase):
    def test_debt_type_enum_contains_supported_values(self):
        debt_type = Debt.__table__.c.debt_type.type

        self.assertEqual(
            tuple(debt_type.enums),
            ('credit_card', 'split', 'mortgage'),
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

    def test_activity_log_contains_request_context_columns(self):
        columns = ActivityLog.__table__.c

        self.assertIn('ip_address', columns)
        self.assertEqual(columns.ip_address.type.length, 100)
        self.assertIn('user_agent', columns)


if __name__ == '__main__':
    unittest.main()
