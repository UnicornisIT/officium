"""Personal delivery schedule and screen privacy; preserve legacy defaults."""
from alembic import op
import sqlalchemy as sa

revision = '20261122_personal_settings'
down_revision = '20261121_vehicle_identifiers'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('notification_timezone', sa.String(64), nullable=True))
    op.add_column('users', sa.Column('notification_delivery_time', sa.String(5), nullable=True))
    op.add_column('users', sa.Column('notification_test_requested_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('hide_amounts_on_open', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    raise RuntimeError('Downgrade is disabled to preserve personal settings.')
