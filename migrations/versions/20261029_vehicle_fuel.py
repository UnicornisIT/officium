"""Fuel history; forward-only to preserve refueling records."""
from alembic import op
import sqlalchemy as sa

revision = '20261029_vehicle_fuel'
down_revision = '20261029_vehicle_maintenance'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicle_refuelings',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name='fk_refueling_vehicle', ondelete='CASCADE'), nullable=False),
        sa.Column('refuel_date', sa.Date(), nullable=False),
        sa.Column('odometer_km', sa.Integer()),
        sa.Column('liters', sa.Numeric(10, 3), nullable=False),
        sa.Column('price_per_liter', sa.Numeric(10, 2), nullable=False),
        sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('payer', sa.String(20), nullable=False),
        sa.Column('expense_id', sa.Integer(), sa.ForeignKey('expenses.id', name='fk_refueling_expense', ondelete='SET NULL')),
        sa.Column('vehicle_cost_id', sa.Integer(), sa.ForeignKey('vehicle_costs.id', name='fk_refueling_cost', ondelete='SET NULL')),
        sa.Column('is_full_tank', sa.Boolean(), nullable=False),
        sa.Column('station_name', sa.String(150)),
        sa.Column('comment', sa.String(2000)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('vehicle_id', 'refuel_date', name='uq_vehicle_refueling_date'),
        sa.CheckConstraint('liters > 0', name='ck_refueling_liters_positive'),
        sa.CheckConstraint('price_per_liter > 0', name='ck_refueling_price_positive'),
        sa.CheckConstraint('total_amount > 0', name='ck_refueling_total_positive'),
        sa.CheckConstraint('odometer_km >= 0 OR odometer_km IS NULL', name='ck_refueling_odometer_nonnegative'),
        sa.CheckConstraint("payer IN ('self', 'organization')", name='ck_refueling_payer'),
    )
    op.create_index('ix_vehicle_refuelings_vehicle_id', 'vehicle_refuelings', ['vehicle_id'])


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled to preserve refueling history.')
