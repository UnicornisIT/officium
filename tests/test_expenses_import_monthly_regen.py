import io
import re
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from app.models import Expense, User
from app.services.expense_service import create_expense, update_expense
from app.services.monthly_expenses_service import (
    generate_monthly_expenses_from_start_date,
)
from extensions import db


class ExpensesImportMonthlyRegenTestCase(unittest.TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory(prefix="officium-monthly-import-")
        self.addCleanup(storage.cleanup)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "monthly-import-tests",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_ENGINE_OPTIONS": {},
                "WTF_CSRF_ENABLED": False,
                "IMPORT_STORAGE_DIR": storage.name,
                "IMPORT_DISK_RESERVE_BYTES": 0,
                "MONTHLY_EXPENSES_TODAY": date(2025, 2, 1),
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            user = User(telegram_id=777, first_name="Import", role="user")
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
            expense = create_expense(
                user_id=user.id,
                amount="750",
                category="communication",
                title="Beeline",
                expense_date="2025-01-01",
                payment_method="card",
                is_monthly=True,
            )
            self.group_id = expense.monthly_group_id
        self.app.config["MONTHLY_EXPENSES_TODAY"] = date(2025, 3, 20)
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def import_statement(self, invalid_second_row=False, expense_date="2025-02-15"):
        statement_date = date.fromisoformat(expense_date).strftime("%d.%m.%Y")
        statement = (
            "Дата операции;Описание операции;Сумма операции;Категория;Карта\n"
            f"{statement_date};Beeline;-900,00;Связь;*1234\n"
        )
        if invalid_second_row:
            statement += "16.02.2025;Groceries;-100,00;Супермаркеты;*1234\n"
        preview = self.client.post(
            "/expenses/import",
            data={
                "statement_file": (
                    io.BytesIO(statement.encode("cp1251")),
                    "statement.csv",
                ),
            },
        )
        self.assertEqual(preview.status_code, 200)
        html = preview.get_data(as_text=True)
        self.assertRegex(html, r'name="action_0"[\s\S]*value="update_monthly" selected')
        token = re.search(r'name="import_token" value="([^"]+)"', html).group(1)
        data = {
            "import_token": token,
            "row_count": "2" if invalid_second_row else "1",
            "action_0": "update_monthly",
            "expense_date_0": expense_date,
            "title_0": "Beeline",
            "category_0": "communication",
            "amount_0": "900.00",
            "payment_method_0": "card",
            "comment_0": "Statement import",
        }
        if invalid_second_row:
            data.update(action_1="create", amount_1="0")
        return self.client.post("/expenses/import/confirm", data=data)

    def test_import_updates_occurrence_and_generates_missing_months(self):
        response = self.import_statement()
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            expenses = (
                Expense.query.filter_by(monthly_group_id=self.group_id)
                .order_by(
                    Expense.expense_date,
                )
                .all()
            )
            self.assertEqual(
                [row.expense_date for row in expenses],
                [
                    date(2025, 1, 1),
                    date(2025, 2, 15),
                    date(2025, 3, 15),
                ],
            )
            self.assertEqual(
                [row.amount for row in expenses],
                [
                    Decimal("750.00"),
                    Decimal("900.00"),
                    Decimal("900.00"),
                ],
            )
            self.assertEqual(expenses[2].monthly_anchor_day, 15)
            self.assertEqual(expenses[2].comment, "Statement import")
            self.assertEqual(expenses[2].generated_from_id, expenses[0].id)

    def test_invalid_later_row_rolls_back_update_and_generated_copies(self):
        response = self.import_statement(invalid_second_row=True)
        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            expenses = Expense.query.order_by(Expense.expense_date).all()
            self.assertEqual(
                [row.expense_date for row in expenses],
                [
                    date(2025, 1, 1),
                    date(2025, 2, 1),
                ],
            )
            self.assertTrue(all(row.amount == Decimal("750.00") for row in expenses))

    def generate_existing_copies(self):
        with self.app.app_context():
            root = Expense.query.filter_by(
                monthly_group_id=self.group_id, generated_from_id=None
            ).one()
            generate_monthly_expenses_from_start_date(root.id, end_month="2025-05")
            return {
                row.generated_for_month: row.id
                for row in Expense.query.filter_by(monthly_group_id=self.group_id)
            }

    def test_import_refreshes_existing_future_copies_without_rewriting_history(self):
        original_ids = self.generate_existing_copies()
        self.assertEqual(self.import_statement().status_code, 302)
        with self.app.app_context():
            rows = {
                row.generated_for_month: row
                for row in Expense.query.filter_by(monthly_group_id=self.group_id)
            }
            self.assertEqual(
                {month: row.id for month, row in rows.items()}, original_ids
            )
            self.assertEqual(rows["2025-02"].expense_date, date(2025, 2, 15))
            self.assertFalse(rows["2025-02"].monthly_is_automatic)
            self.assertEqual(rows["2025-03"].expense_date, date(2025, 3, 1))
            self.assertEqual(rows["2025-03"].amount, Decimal("750.00"))
            for month in (4, 5):
                row = rows[f"2025-{month:02}"]
                self.assertEqual(row.expense_date, date(2025, month, 15))
                self.assertEqual(row.amount, Decimal("900.00"))
                self.assertEqual(row.comment, "Statement import")
                self.assertEqual(row.monthly_anchor_day, 15)
                self.assertEqual(
                    row.monthly_settings_updated_at,
                    rows["2025-02"].monthly_settings_updated_at,
                )

    def test_import_preserves_explicitly_edited_future_occurrence(self):
        ids = self.generate_existing_copies()
        with self.app.app_context():
            update_expense(
                user_id=self.user_id,
                expense_id=ids["2025-05"],
                amount="1100",
                category="communication",
                title="Beeline",
                expense_date="2025-05-07",
                payment_method="cash",
                comment="Separate correction",
                is_monthly=True,
            )
        self.assertEqual(self.import_statement().status_code, 302)
        with self.app.app_context():
            april = db.session.get(Expense, ids["2025-04"])
            may = db.session.get(Expense, ids["2025-05"])
            self.assertEqual(april.expense_date, date(2025, 4, 15))
            self.assertEqual(may.expense_date, date(2025, 5, 7))
            self.assertEqual(may.amount, Decimal("1100.00"))
            self.assertEqual(may.comment, "Separate correction")

    def test_invalid_later_row_rolls_back_existing_future_copies(self):
        ids = self.generate_existing_copies()
        self.assertEqual(
            self.import_statement(invalid_second_row=True).status_code, 400
        )
        with self.app.app_context():
            for month, expense_id in ids.items():
                row = db.session.get(Expense, expense_id)
                self.assertEqual(row.expense_date, date.fromisoformat(month + "-01"))
                self.assertEqual(row.amount, Decimal("750.00"))

    def test_import_preserves_manual_correction_with_matching_timestamp(self):
        ids = self.generate_existing_copies()
        with self.app.app_context():
            timestamp = db.session.get(
                Expense, ids["2025-02"]
            ).monthly_settings_updated_at
            with patch("app.services.expense_service.utc_now", return_value=timestamp):
                update_expense(
                    user_id=self.user_id,
                    expense_id=ids["2025-05"],
                    amount="1100",
                    category="communication",
                    title="Beeline",
                    expense_date="2025-05-07",
                    payment_method="cash",
                    comment="Separate correction",
                    is_monthly=True,
                )
        self.assertEqual(self.import_statement().status_code, 302)
        with self.app.app_context():
            may = db.session.get(Expense, ids["2025-05"])
            self.assertEqual(may.amount, Decimal("1100.00"))
            self.assertEqual(may.expense_date, date(2025, 5, 7))

    def test_future_dates_keep_anchor_after_short_month(self):
        ids = self.generate_existing_copies()
        self.assertEqual(
            self.import_statement(expense_date="2025-03-31").status_code, 302
        )
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Expense, ids["2025-04"]).expense_date, date(2025, 4, 30)
            )
            self.assertEqual(
                db.session.get(Expense, ids["2025-05"]).expense_date, date(2025, 5, 31)
            )

    def test_import_preserves_occurrence_dated_today(self):
        ids = self.generate_existing_copies()
        with self.app.app_context():
            db.session.get(Expense, ids["2025-03"]).expense_date = date(2025, 3, 20)
            db.session.commit()
        self.assertEqual(self.import_statement().status_code, 302)
        with self.app.app_context():
            today = db.session.get(Expense, ids["2025-03"])
            self.assertEqual(today.expense_date, date(2025, 3, 20))
            self.assertEqual(today.amount, Decimal("750.00"))
