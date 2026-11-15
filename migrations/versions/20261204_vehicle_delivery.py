"""Track vehicle notification attempts and delivery outcomes."""

from alembic import op
import sqlalchemy as sa


revision = '20261204_vehicle_delivery'
down_revision = '20261203_bank_import_entries'
branch_labels = None
depends_on = None

TABLE = 'vehicle_notification_log'
INDEX = 'ix_vehicle_notification_reservation'


def upgrade():
    op.add_column(
        TABLE,
        sa.Column('status', sa.String(16), nullable=False, server_default='unknown'),
    )
    op.add_column(
        TABLE,
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(TABLE, sa.Column('attempted_at', sa.DateTime()))
    op.add_column(TABLE, sa.Column('retry_at', sa.DateTime()))
    # The legacy false flag also covered interrupted sends and ambiguous responses.
    log = sa.table(
        TABLE, sa.column('status', sa.String()), sa.column('delivered', sa.Boolean())
    )
    op.execute(
        log.update()
        .where(log.c.delivered == sa.true())
        .values(status=op.inline_literal('accepted'))
    )
    op.create_index(INDEX, TABLE, ['status', 'attempted_at'])


def downgrade():
    op.drop_index(INDEX, table_name=TABLE)
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column('retry_at')
        batch.drop_column('attempted_at')
        batch.drop_column('attempt_count')
        batch.drop_column('status')
