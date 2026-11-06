"""Create or remove a debt in the disposable Playwright database."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import gettempdir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", type=int, metavar="DEBT_ID")
    args = parser.parse_args()
    database = Path(gettempdir()) / "officium-playwright.sqlite"
    if not database.is_file():
        parser.error(
            "Expected the disposable database created by scripts/e2e_server.py"
        )

    with (
        closing(
            sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)
        ) as connection,
        connection,
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        user = connection.execute(
            "SELECT id FROM users WHERE telegram_id = -1000000001"
        ).fetchone()
        if user is None:
            parser.error("Log in as the Playwright test user first")
        identity = (user[0], "Проверка остатка", "Тестовый кредит")
        if args.delete is not None:
            debt = connection.execute(
                "SELECT id FROM debts WHERE user_id = ? AND bank_name = ? AND product_name = ? AND id = ?",
                (*identity, args.delete),
            ).fetchone()
            if debt is None:
                parser.error("Expected the debt created for this payment test")
            connection.execute("DELETE FROM payments WHERE debt_id = ?", debt)
            connection.execute("DELETE FROM debts WHERE id = ?", debt)
            return

        created = connection.execute(
            """INSERT INTO debts (
                user_id, bank_name, product_name, debt_type, total_amount,
                remaining_amount, minimum_payment, interest_rate, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'consumer_credit', 25000, 25000, 2500, 0, 'active',
                      CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            identity,
        )
    print(created.lastrowid)


if __name__ == "__main__":
    main()
