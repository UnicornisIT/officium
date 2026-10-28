"""Preserve organization spending titles and payment methods."""
from alembic import op
import sqlalchemy as sa
revision = '20261028_vehicle_cost_details'
down_revision = '20261028_vehicle_costs'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicle_costs') as batch:
        batch.add_column(sa.Column('title', sa.String(150), nullable=True))
        batch.add_column(sa.Column('payment_method', sa.String(20), nullable=True))


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled to preserve vehicle spending details.')
