"""Удалять платежи вместе с долгом на уровне базы данных."""

from alembic import op
import sqlalchemy as sa


revision = '20261128_payment_cascade'
down_revision = '20261127_debt_payment_plans'
branch_labels = None
depends_on = None

FK_NAME = 'fk_payments_debt_id_debts'
NAMING_CONVENTION = {
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'
}


def _replace_foreign_key(ondelete):
    foreign_keys = [
        item
        for item in sa.inspect(op.get_bind()).get_foreign_keys('payments')
        if item['constrained_columns'] == ['debt_id']
        and item['referred_table'] == 'debts'
    ]
    # Имена нужны для исходного безымянного ключа SQLite; данные сохраняет batch.
    # Каскад проверяет test_payment_cascade_migration_preserves_data_and_deletes_payments.
    with op.batch_alter_table('payments', naming_convention=NAMING_CONVENTION) as batch:
        for foreign_key in foreign_keys:
            batch.drop_constraint(foreign_key['name'] or FK_NAME, type_='foreignkey')
        batch.create_foreign_key(
            FK_NAME, 'debts', ['debt_id'], ['id'], ondelete=ondelete
        )


def upgrade():
    _replace_foreign_key('CASCADE')


def downgrade():
    _replace_foreign_key(None)
