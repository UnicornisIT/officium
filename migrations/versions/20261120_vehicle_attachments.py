"""Private vehicle image attachments."""
from alembic import op
import sqlalchemy as sa

revision = '20261120_vehicle_attachments'
down_revision = '20261106_vehicle_notifications'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicle_attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name='fk_attachment_user', ondelete='CASCADE'), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name='fk_attachment_vehicle', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('entity_id', sa.Integer()),
        sa.Column('file_path', sa.String(255), nullable=False),
        sa.Column('original_name', sa.String(255)),
        sa.Column('mime_type', sa.String(50), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer()), sa.Column('height', sa.Integer()),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('caption', sa.String(500)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('vehicle','defect','document','expense','vehicle_cost','service_record')", name='ck_attachment_kind'),
        sa.CheckConstraint('size_bytes > 0 AND size_bytes <= 5242880', name='ck_attachment_size'),
        sa.CheckConstraint("mime_type IN ('image/jpeg','image/png','image/webp')", name='ck_attachment_mime'),
        sa.UniqueConstraint('user_id', 'sha256', name='uq_attachment_sha256_user'))
    op.create_index('ix_vehicle_attachments_user_id', 'vehicle_attachments', ['user_id'])
    op.create_index('ix_attachment_user_vehicle', 'vehicle_attachments', ['user_id', 'vehicle_id'])
    op.create_index('ix_attachment_entity', 'vehicle_attachments', ['kind', 'entity_id'])


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled to preserve attachments.')
