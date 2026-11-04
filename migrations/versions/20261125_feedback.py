"""Обращения пользователей из Telegram."""

from alembic import op
import sqlalchemy as sa


revision = '20261125_feedback'
down_revision = '20261124_monthly_reminders'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feedback_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), server_default='new', nullable=False),
        sa.Column('submission_key', sa.String(32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('idea', 'bug')", name='ck_feedback_reports_kind'),
        sa.CheckConstraint(
            "status IN ('new', 'in_progress', 'closed')",
            name='ck_feedback_reports_status',
        ),
        sa.UniqueConstraint('submission_key'),
    )
    op.create_index('ix_feedback_reports_user_id', 'feedback_reports', ['user_id'])
    op.create_index(
        'ix_feedback_reports_status_created_at',
        'feedback_reports',
        ['status', 'created_at'],
    )


def downgrade():
    op.drop_table('feedback_reports')
