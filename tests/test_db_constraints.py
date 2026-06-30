import pytest
from sqlalchemy import inspect

from app.database import Base


@pytest.mark.parametrize(
    "table,index_name",
    [
        ("courses", "ix_courses_professor_id"),
        ("units", "ix_units_course_id"),
        ("sessions", "ix_sessions_disease_id"),
        ("disease_documents", "ix_disease_documents_uploaded_by"),
    ],
)
def test_expected_index_defined(table, index_name):
    idx_names = {ix.name for ix in Base.metadata.tables[table].indexes}
    assert index_name in idx_names, f"{index_name} missing on {table}"
