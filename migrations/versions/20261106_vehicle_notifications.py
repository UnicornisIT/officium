"""Vehicle notification preferences and durable delivery reservations."""
from alembic import op
import sqlalchemy as sa

revision = '20261106_vehicle_notifications'
down_revision = '20261105_vehicle_documents'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('vehicle_notifications_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table('vehicle_notification_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name='fk_notification_user', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name='fk_notification_vehicle', ondelete='SET NULL')),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('delivered', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('user_id', 'kind', 'payload_hash', name='uq_notification_log_day'))
    op.create_index('ix_vehicle_notification_log_user_id', 'vehicle_notification_log', ['user_id'])


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled to preserve delivery history.')
