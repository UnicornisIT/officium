"""Persist per-recipient update delivery reservations to prevent duplicate sends."""
from alembic import op
import sqlalchemy as sa

revision = '20261123_update_notifications'
down_revision = '20261122_personal_settings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'update_notification_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_key', sa.String(180), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('failure_kind', sa.String(40), nullable=True),
        sa.Column('attempted_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('event_key', 'user_id', name='uq_update_notification_recipient'),
    )


def downgrade():
    raise RuntimeError('Downgrade is disabled to preserve notification delivery history.')
