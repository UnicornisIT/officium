"""Record transaction identities for confirmed bank statement rows."""

from alembic import op
import sqlalchemy as sa


revision = '20261203_bank_import_entries'
down_revision = '20261202_expense_repayment'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bank_import_entries',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('file_row_key', sa.String(64), nullable=False),
        sa.Column('transaction_key', sa.String(64)),
        sa.Column('fallback_key', sa.String(64), nullable=False),
        sa.Column('expense_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['expense_id'], ['expenses.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'file_row_key'),
        sa.UniqueConstraint(
            'user_id', 'transaction_key', name='uq_bank_import_transaction'
        ),
    )
    op.create_index(
        'ix_bank_import_fallback', 'bank_import_entries', ['user_id', 'fallback_key']
    )
    op.create_index(
        'ix_bank_import_entries_expense_id', 'bank_import_entries', ['expense_id']
    )


def downgrade():
    op.drop_table('bank_import_entries')
