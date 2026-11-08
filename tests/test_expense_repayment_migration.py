import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from tests.test_debt_accrual_migration import VERSIONS, load_migration, mysql_sql


class ExpenseRepaymentMigrationTests(unittest.TestCase):
    def test_sqlite_dry_run_preserves_history_without_inferred_links(self):
        module = load_migration(VERSIONS / "20261202_expense_repayment_debt.py")
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(text("CREATE TABLE debts (id INTEGER PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE expenses (id INTEGER PRIMARY KEY, amount NUMERIC(12,2), title TEXT)"
                )
            )
            connection.execute(
                text("INSERT INTO expenses VALUES (1, 15000.01, 'Loan')")
            )
            before = connection.execute(text("SELECT * FROM expenses")).all()
            connection.execute(
                text(
                    "CREATE TABLE receipts (id INTEGER PRIMARY KEY, expense_id INTEGER "
                    "REFERENCES expenses(id) ON DELETE CASCADE)"
                )
            )
            connection.execute(text("INSERT INTO receipts VALUES (1, 1)"))
            connection.commit()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            self.assertEqual(
                connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1
            )
            fk = inspect(connection).get_foreign_keys("expenses")[0]
            self.assertEqual(fk["name"], "fk_expenses_repayment_debt_id")
            self.assertEqual(fk["options"]["ondelete"], "SET NULL")
            self.assertEqual(
                connection.execute(
                    text("SELECT repayment_debt_id FROM expenses")
                ).scalar_one(),
                None,
            )
            self.assertEqual(
                connection.execute(
                    text("SELECT id, amount, title FROM expenses")
                ).all(),
                before,
            )
            connection.execute(text("INSERT INTO debts VALUES (1)"))
            connection.execute(text("UPDATE expenses SET repayment_debt_id=1"))
            connection.execute(text("DELETE FROM debts WHERE id=1"))
            self.assertIsNone(
                connection.execute(
                    text("SELECT repayment_debt_id FROM expenses")
                ).scalar_one()
            )
            connection.commit()
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            self.assertEqual(
                connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1
            )
            self.assertEqual(
                connection.execute(text("SELECT * FROM expenses")).all(), before
            )
            self.assertEqual(
                connection.execute(text("SELECT * FROM receipts")).all(), [(1, 1)]
            )
            self.assertNotIn(
                "repayment_debt_id",
                {item["name"] for item in inspect(connection).get_columns("expenses")},
            )

    def test_mysql_offline_ddl_has_no_backfill(self):
        module = load_migration(VERSIONS / "20261202_expense_repayment_debt.py")
        sql = mysql_sql(module, "upgrade")
        self.assertIn("ADD COLUMN repayment_debt_id INTEGER", sql)
        self.assertIn("ON DELETE SET NULL", sql)
        self.assertNotIn("UPDATE", sql)
        self.assertNotIn("DELETE FROM", sql)
