"""Add monthly expenses functionality to expenses table

Revision ID: 20260517_monthly_expenses
Revises: 20260503_debt_type
Create Date: 2026-05-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260517_monthly_expenses'
down_revision = '20260503_debt_type'
branch_labels = None
depends_on = None


def table_exists(table_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def column_exists(table_name, column_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not table_exists(table_name):
        return False
    return column_name in [col['name'] for col in inspector.get_columns(table_name)]


def upgrade():
    if table_exists('expenses'):
        if not column_exists('expenses', 'is_monthly'):
            op.add_column(
                'expenses',
                sa.Column('is_monthly', sa.Boolean(), nullable=False, server_default='0')
            )
        if not column_exists('expenses', 'monthly_group_id'):
            op.add_column(
                'expenses',
                sa.Column('monthly_group_id', sa.String(length=36), nullable=True)
            )
        if not column_exists('expenses', 'generated_from_id'):
            op.add_column(
                'expenses',
                sa.Column('generated_from_id', sa.Integer(), nullable=True)
            )
        if not column_exists('expenses', 'generated_for_month'):
            op.add_column(
                'expenses',
                sa.Column('generated_for_month', sa.String(length=7), nullable=True)
            )
        
        # Add foreign key for generated_from_id (self-referencing)
        if table_exists('expenses'):
            try:
                op.create_foreign_key(
                    'fk_expenses_generated_from_id',
                    'expenses',
                    'expenses',
                    ['generated_from_id'],
                    ['id'],
                    ondelete='SET NULL'
                )
            except Exception:
                # Foreign key might already exist or constraint naming might differ
                pass


def downgrade():
    raise RuntimeError(
        'Downgrading monthly expenses is disabled to preserve existing data. '
        'Use manual rollback if necessary.'
    )
