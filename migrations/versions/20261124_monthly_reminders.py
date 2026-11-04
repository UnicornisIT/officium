"""Настройки и журнал напоминаний о ежемесячных расходах."""
from alembic import op
import sqlalchemy as sa


revision = '20261124_monthly_reminders'
down_revision = '20261123_update_notifications'
branch_labels = None
depends_on = None


def upgrade():
    if not op.get_context().as_sql:
        # Старые локальные БД могли быть созданы без SET NULL до ввода миграций.
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys('expenses'):
            if (foreign_key['constrained_columns'] == ['generated_from_id']
                    and (foreign_key.get('options', {}).get('ondelete') or '').upper() != 'SET NULL'):
                convention = {'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'}
                name = foreign_key['name'] or 'fk_expenses_generated_from_id_expenses'
                with op.batch_alter_table('expenses', naming_convention=convention) as batch_op:
                    batch_op.drop_constraint(name, type_='foreignkey')
                    batch_op.create_foreign_key(name, 'expenses', ['generated_from_id'], ['id'], ondelete='SET NULL')
    op.create_table(
        'monthly_expense_reminder_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monthly_expense_id', sa.Integer(), sa.ForeignKey('expenses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('monthly_expense_id', 'due_date', name='uq_monthly_reminder_occurrence'),
    )
    op.create_index('ix_monthly_expense_reminder_logs_user_id', 'monthly_expense_reminder_logs', ['user_id'])
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('reminder_enabled', sa.Boolean(), server_default=sa.true(), nullable=False))
        batch_op.add_column(sa.Column('reminder_time', sa.String(length=5), server_default='10:00', nullable=False))
        batch_op.add_column(sa.Column('reminder_days_before', sa.SmallInteger(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('timezone', sa.String(length=64), server_default='Europe/Moscow', nullable=False))


def downgrade():
    op.drop_table('monthly_expense_reminder_logs')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('timezone')
        batch_op.drop_column('reminder_days_before')
        batch_op.drop_column('reminder_time')
        batch_op.drop_column('reminder_enabled')
