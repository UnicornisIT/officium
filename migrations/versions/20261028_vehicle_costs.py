"""Separate organization vehicle costs from personal finances."""
from alembic import op
import sqlalchemy as sa
revision = '20261028_vehicle_costs'
down_revision = '20261027_vehicle_core'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('expenses') as batch:
        batch.add_column(sa.Column('vehicle_cost_kind', sa.String(20)))
    op.create_table('vehicle_costs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cost_date', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('comment', sa.String(2000)),
        sa.Column('paid_by_driver', sa.Boolean(), nullable=False),
        sa.Column('reimbursed_at', sa.Date()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_vehicle_costs_amount'),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name='fk_vehicle_costs_vehicle'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_vehicle_costs_user'))
    for column in ('vehicle_id', 'user_id', 'cost_date'):
        op.create_index('ix_vehicle_costs_' + column, 'vehicle_costs', [column])


def downgrade():
    with op.batch_alter_table('expenses') as batch:
        batch.drop_column('vehicle_cost_kind')
    op.drop_table('vehicle_costs')
