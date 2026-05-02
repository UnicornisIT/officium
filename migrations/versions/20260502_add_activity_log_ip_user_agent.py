"""Add ip_address and user_agent to activity_logs

Revision ID: 20260502_add_activity_log_ip_user_agent
Revises: 20260502_add_mortgage_debt_type
Create Date: 2026-05-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260502_add_activity_log_ip_user_agent'
down_revision = '20260502_add_mortgage_debt_type'
branch_labels = None
depends_on = None


def _column_exists(connection, table_name, column_name):
    dialect_name = connection.dialect.name
    if dialect_name == 'mysql':
        result = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column"
            ),
            {"table": table_name, "column": column_name},
        )
        return result.scalar() > 0

    if dialect_name == 'sqlite':
        result = connection.execute(sa.text(f"PRAGMA table_info('{table_name}')"))
        return any(row[1] == column_name for row in result.fetchall())

    return False


def upgrade():
    connection = op.get_bind()
    if not _column_exists(connection, 'activity_logs', 'ip_address'):
        op.execute(
            "ALTER TABLE activity_logs ADD COLUMN ip_address VARCHAR(45) NULL AFTER description"
        )
    if not _column_exists(connection, 'activity_logs', 'user_agent'):
        op.execute(
            "ALTER TABLE activity_logs ADD COLUMN user_agent TEXT NULL AFTER ip_address"
        )


def downgrade():
    connection = op.get_bind()
    if _column_exists(connection, 'activity_logs', 'user_agent'):
        op.execute("ALTER TABLE activity_logs DROP COLUMN user_agent")
    if _column_exists(connection, 'activity_logs', 'ip_address'):
        op.execute("ALTER TABLE activity_logs DROP COLUMN ip_address")
