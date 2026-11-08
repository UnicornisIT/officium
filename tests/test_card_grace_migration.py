"""Check card conditions migration without touching application databases."""

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from tests.test_debt_accrual_migration import VERSIONS, load_migration, mysql_sql


def test_grace_policy_keeps_legacy_rows_unknown():
    migration = load_migration(VERSIONS / "20261202_card_grace_policy.py")
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE debts (id INTEGER PRIMARY KEY, grace_period_end_date DATE)"
            )
        )
        connection.execute(
            text("INSERT INTO debts VALUES (1, NULL), (2, '2026-08-31')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(text("SELECT * FROM debts ORDER BY id")).all() == [
            (1, None, None),
            (2, "2026-08-31", None),
        ]
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(text("SELECT COUNT(*) FROM debts")).scalar() == 2
    engine.dispose()


def test_grace_policy_mysql_offline_ddl():
    migration = load_migration(VERSIONS / "20261202_card_grace_policy.py")
    sql = mysql_sql(migration, "upgrade")
    assert "ADD COLUMN grace_policy VARCHAR(20)" in sql
    assert "UPDATE" not in sql
