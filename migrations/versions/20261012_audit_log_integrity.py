"""Keep activity-log entries when a user is deleted and record who acted.

Revision ID: 20261012_audit_log
Revises: 20261009_monthly_settings
Create Date: 2026-10-12 21:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20261012_audit_log'
down_revision = '20261009_monthly_settings'
branch_labels = None
depends_on = None

FK_NAME = 'fk_activity_logs_user_id'


def _inspector():
    return sa.inspect(op.get_bind())


def _columns():
    return {column['name'] for column in _inspector().get_columns('activity_logs')}


def _user_foreign_keys():
    return [
        item for item in _inspector().get_foreign_keys('activity_logs')
        if item.get('constrained_columns') == ['user_id'] and item.get('referred_table') == 'users'
    ]


def _sqlite_table(ondelete, with_actor_label):
    """Full activity_logs definition used to rebuild the table on SQLite."""
    columns = [
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name=FK_NAME, ondelete=ondelete), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(length=100), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    ]
    if with_actor_label:
        columns.append(sa.Column('actor_label', sa.String(length=64), nullable=True))
    return sa.Table('activity_logs', sa.MetaData(), *columns)


def upgrade():
    bind = op.get_bind()
    if 'actor_label' not in _columns():
        op.add_column('activity_logs', sa.Column('actor_label', sa.String(length=64), nullable=True))

    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table(
            'activity_logs',
            recreate='always',
            copy_from=_sqlite_table('SET NULL', with_actor_label=True),
        ):
            pass
        return

    for foreign_key in _user_foreign_keys():
        if foreign_key.get('options', {}).get('ondelete', '').upper() == 'SET NULL':
            return
        op.drop_constraint(foreign_key['name'], 'activity_logs', type_='foreignkey')
    op.create_foreign_key(FK_NAME, 'activity_logs', 'users', ['user_id'], ['id'], ondelete='SET NULL')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table(
            'activity_logs',
            recreate='always',
            copy_from=_sqlite_table(None, with_actor_label=True),
        ) as batch_op:
            batch_op.drop_column('actor_label')
        return

    for foreign_key in _user_foreign_keys():
        op.drop_constraint(foreign_key['name'], 'activity_logs', type_='foreignkey')
    op.create_foreign_key(FK_NAME, 'activity_logs', 'users', ['user_id'], ['id'])
    if 'actor_label' in _columns():
        op.drop_column('activity_logs', 'actor_label')
