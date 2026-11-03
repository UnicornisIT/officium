"""Allow local body/chassis identifiers alongside standard VINs."""
from alembic import op
import sqlalchemy as sa

revision = '20261121_vehicle_identifiers'
down_revision = '20261120_vehicle_attachments'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles') as batch:
        batch.alter_column('vin', existing_type=sa.String(17),
                           type_=sa.String(40), existing_nullable=True)


def downgrade():
    raise RuntimeError('Downgrade is disabled to preserve body/chassis identifiers.')
