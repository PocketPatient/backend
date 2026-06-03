"""one active session per user/course

Revision ID: c4a1f7e9b2d0
Revises: b7d991975e12
Create Date: 2026-06-02 22:45:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4a1f7e9b2d0'
down_revision: Union[str, Sequence[str], None] = 'b7d991975e12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enforce at most one active session per (user, course)."""
    op.create_index(
        "uq_one_active_session_per_user_course",
        "sessions",
        ["user_id", "course_id"],
        unique=True,
        postgresql_where="status = 'active'",
    )


def downgrade() -> None:
    op.drop_index("uq_one_active_session_per_user_course", table_name="sessions")
