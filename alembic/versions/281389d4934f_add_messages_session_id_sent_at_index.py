"""add messages session_id sent_at index

Revision ID: 281389d4934f
Revises: 4522b2be2830
Create Date: 2026-06-21 21:32:32.920209

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '281389d4934f'
down_revision: Union[str, Sequence[str], None] = '4522b2be2830'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_messages_session_id_sent_at",
        "messages",
        ["session_id", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_session_id_sent_at", table_name="messages")
