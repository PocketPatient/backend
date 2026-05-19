"""make_role_nullable

Revision ID: 000573b034f5
Revises: aab0c0019f65
Create Date: 2026-05-18 15:51:00.089386

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '000573b034f5'
down_revision: Union[str, Sequence[str], None] = 'aab0c0019f65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('users', 'role',
               existing_type=postgresql.ENUM('student', 'professor', 'admin', name='userrole'),
               nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('users', 'role',
               existing_type=postgresql.ENUM('student', 'professor', 'admin', name='userrole'),
               nullable=False)
