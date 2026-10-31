"""Vehicle documents and deadlines."""
from alembic import op
import sqlalchemy as sa

revision = '20261105_vehicle_documents'
down_revision = '20261029_vehicle_fuel'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vehicle_documents',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name='fk_document_vehicle', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('title', sa.String(150)),
        sa.Column('issued_date', sa.Date()),
        sa.Column('expires_at', sa.Date()),
        sa.Column('amount', sa.Numeric(12, 2)),
        sa.Column('expense_id', sa.Integer(), sa.ForeignKey('expenses.id', name='fk_document_expense', ondelete='SET NULL')),
        sa.Column('vehicle_cost_id', sa.Integer(), sa.ForeignKey('vehicle_costs.id', name='fk_document_cost', ondelete='SET NULL')),
        sa.Column('comment', sa.String(2000)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('vehicle_id', 'kind', name='uq_vehicle_document_active_kind'),
        sa.CheckConstraint("kind IN ('osago','kasko','tech_inspection','pts','sts','license','other')", name='ck_vehicle_document_kind'),
        sa.CheckConstraint('expires_at IS NULL OR issued_date IS NULL OR expires_at >= issued_date', name='ck_vehicle_document_dates'),
        sa.CheckConstraint('amount IS NULL OR amount > 0', name='ck_vehicle_document_amount_positive'),
    )
    op.create_index('ix_vehicle_documents_vehicle_id', 'vehicle_documents', ['vehicle_id'])


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled to preserve documents.')
