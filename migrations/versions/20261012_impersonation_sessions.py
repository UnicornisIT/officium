"""Track administrator impersonation sessions and link journal entries to them.

Revision ID: 20261012_impersonation
Revises: 20261012_audit_log
Create Date: 2026-10-12 22:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261012_impersonation'
down_revision = '20261012_audit_log'
branch_labels = None
depends_on = None

TABLE = 'impersonation_sessions'
LOG_FK = 'fk_activity_logs_impersonation_session_id'


def _inspector():
    return sa.inspect(op.get_bind())


def _log_columns():
    return {column['name'] for column in _inspector().get_columns('activity_logs')}


def _sqlite_activity_logs(with_session_link):
    """Full activity_logs definition used to rebuild the table on SQLite."""
    columns = [
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id', sa.Integer(),
            sa.ForeignKey('users.id', name='fk_activity_logs_user_id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(length=100), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('actor_label', sa.String(length=64), nullable=True),
    ]
    if with_session_link:
        columns.append(sa.Column(
            'impersonation_session_id', sa.Integer(),
            sa.ForeignKey(f'{TABLE}.id', name=LOG_FK, ondelete='SET NULL'),
            nullable=True,
        ))
    return sa.Table('activity_logs', sa.MetaData(), *columns)


def upgrade():
    bind = op.get_bind()
    if TABLE not in _inspector().get_table_names():
        op.create_table(
            TABLE,
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('admin_user_id', sa.Integer(), nullable=True),
            sa.Column('actor_label', sa.String(length=64), nullable=False),
            sa.Column('target_user_id', sa.Integer(), nullable=True),
            sa.Column('target_label', sa.String(length=64), nullable=False),
            sa.Column('reason', sa.String(length=200), nullable=True),
            sa.Column('read_only', sa.Boolean(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('ended_at', sa.DateTime(), nullable=True),
            sa.Column('end_reason', sa.String(length=20), nullable=True),
            sa.Column('ip_start', sa.String(length=100), nullable=True),
            sa.Column('ip_end', sa.String(length=100), nullable=True),
            sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_impersonation_sessions_active', TABLE, ['ended_at', 'expires_at'])

    if 'impersonation_session_id' in _log_columns():
        return
    if bind.dialect.name == 'sqlite':
        op.add_column('activity_logs', sa.Column('impersonation_session_id', sa.Integer(), nullable=True))
        with op.batch_alter_table(
            'activity_logs', recreate='always', copy_from=_sqlite_activity_logs(with_session_link=True),
        ):
            pass
    else:
        op.add_column('activity_logs', sa.Column('impersonation_session_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            LOG_FK, 'activity_logs', TABLE, ['impersonation_session_id'], ['id'], ondelete='SET NULL',
        )


def downgrade():
    # The impersonation_sessions table is kept (project policy: migrations never
    # drop tables); the previous release simply does not read it. Only the
    # journal link is removed so activity_logs matches the previous schema.
    if 'impersonation_session_id' not in _log_columns():
        return
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table(
            'activity_logs', recreate='always', copy_from=_sqlite_activity_logs(with_session_link=True),
        ) as batch_op:
            batch_op.drop_constraint(LOG_FK, type_='foreignkey')
            batch_op.drop_column('impersonation_session_id')
    else:
        op.drop_constraint(LOG_FK, 'activity_logs', type_='foreignkey')
        op.drop_column('activity_logs', 'impersonation_session_id')
