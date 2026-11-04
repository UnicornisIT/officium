"""Allow separate entity attachments to share stored image bytes."""

from alembic import op

revision = "20261126_attachment_dedup"
down_revision = "20261125_feedback"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("vehicle_attachments") as batch:
        batch.drop_constraint("uq_attachment_sha256_user", type_="unique")
        batch.create_index("ix_attachment_sha256_user", ["user_id", "sha256"])


def downgrade():
    with op.batch_alter_table("vehicle_attachments") as batch:
        # Reinstating uniqueness rejects shared hashes rather than discarding attachments.
        batch.create_unique_constraint(
            "uq_attachment_sha256_user", ["user_id", "sha256"]
        )
        batch.drop_index("ix_attachment_sha256_user")
