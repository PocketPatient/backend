"""week07 add patient persona to sessions

Revision ID: 14a1d4e44497
Revises: 90d9039e74bb
Create Date: 2026-07-09 23:51:09.809705

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '14a1d4e44497'
down_revision: Union[str, Sequence[str], None] = '90d9039e74bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add nullable first so existing rows don't violate NOT NULL, backfill them with
    # placeholders, then enforce NOT NULL to match the model. New rows always supply
    # the persona (generated in session_service.create_new_session).
    op.add_column("sessions", sa.Column("patient_name", sa.String(length=100), nullable=True))
    op.add_column("sessions", sa.Column("patient_age", sa.Integer(), nullable=True))
    op.add_column("sessions", sa.Column("patient_gender", sa.String(length=20), nullable=True))

    op.execute(
        "UPDATE sessions SET patient_name = 'Unknown', patient_age = 0, "
        "patient_gender = 'unknown' WHERE patient_name IS NULL"
    )

    op.alter_column("sessions", "patient_name", nullable=False)
    op.alter_column("sessions", "patient_age", nullable=False)
    op.alter_column("sessions", "patient_gender", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sessions", "patient_gender")
    op.drop_column("sessions", "patient_age")
    op.drop_column("sessions", "patient_name")
