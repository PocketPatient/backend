"""week16 indexes cascade check

Revision ID: 90d9039e74bb
Revises: 7892262555f3
Create Date: 2026-06-29 22:43:25.747542

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '90d9039e74bb'
down_revision: Union[str, Sequence[str], None] = '7892262555f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Four new indexes (Tasks 5 & 6)
    op.create_index('ix_courses_professor_id', 'courses', ['professor_id'], unique=False)
    op.create_index('ix_disease_documents_uploaded_by', 'disease_documents', ['uploaded_by'], unique=False)
    op.create_index('ix_sessions_disease_id', 'sessions', ['disease_id'], unique=False)
    op.create_index('ix_units_course_id', 'units', ['course_id'], unique=False)

    # messages.session_id FK — drop and recreate with ON DELETE CASCADE
    op.drop_constraint('messages_session_id_fkey', 'messages', type_='foreignkey')
    op.create_foreign_key(
        'messages_session_id_fkey', 'messages', 'sessions',
        ['session_id'], ['id'], ondelete='CASCADE',
    )

    # quiet-hours paired check constraint (autogenerate does not emit CheckConstraints)
    op.create_check_constraint(
        'ck_quiet_hours_paired', 'users',
        '(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop check constraint
    op.drop_constraint('ck_quiet_hours_paired', 'users', type_='check')

    # messages.session_id FK — restore without ON DELETE CASCADE
    op.drop_constraint('messages_session_id_fkey', 'messages', type_='foreignkey')
    op.create_foreign_key(
        'messages_session_id_fkey', 'messages', 'sessions',
        ['session_id'], ['id'],
    )

    # Drop the four indexes
    op.drop_index('ix_units_course_id', table_name='units')
    op.drop_index('ix_sessions_disease_id', table_name='sessions')
    op.drop_index('ix_disease_documents_uploaded_by', table_name='disease_documents')
    op.drop_index('ix_courses_professor_id', table_name='courses')
