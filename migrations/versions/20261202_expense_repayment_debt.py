"""Add explicit repayment identity without inferring links from legacy data."""

from contextlib import contextmanager

from alembic import op
import sqlalchemy as sa


revision = "20261202_expense_repayment"
down_revision = "20261202_card_grace"
branch_labels = None
depends_on = None


@contextmanager
def _preserve_sqlite_references():
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        yield
        return
    # SQLite batch recreation drops the old table. Disable cascades outside a
    # transaction so dependent receipt/vehicle records survive that drop.
    with op.get_context().autocommit_block():
        enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            yield
        finally:
            connection.exec_driver_sql(f"PRAGMA foreign_keys={int(enabled)}")


def upgrade():
    with _preserve_sqlite_references(), op.batch_alter_table("expenses") as batch:
        batch.add_column(sa.Column("repayment_debt_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_expenses_repayment_debt_id",
            "debts",
            ["repayment_debt_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_expenses_repayment_debt_id", ["repayment_debt_id"])


def downgrade():
    with _preserve_sqlite_references(), op.batch_alter_table("expenses") as batch:
        batch.drop_index("ix_expenses_repayment_debt_id")
        batch.drop_constraint("fk_expenses_repayment_debt_id", type_="foreignkey")
        batch.drop_column("repayment_debt_id")
