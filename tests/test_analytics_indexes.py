from __future__ import annotations

from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.session import Session


def _index_names(model) -> set[str]:
    return {ix.name for ix in model.__table__.indexes}


def test_sessions_have_analytics_indexes():
    names = _index_names(Session)
    # Class-summary aggregations filter on (course_id, status); student summaries
    # scope on (user_id, course_id). Both back the hot analytics paths.
    assert "ix_sessions_course_id_status" in names
    assert "ix_sessions_user_id_course_id" in names


def test_enrollments_have_course_index():
    # Class summary counts/joins enrollments by course_id alone; the existing
    # unique(user_id, course_id) leads with user_id and can't serve this.
    assert "ix_enrollments_course_id" in _index_names(Enrollment)


def test_diseases_have_unit_index():
    # completion_by_unit joins sessions->diseases and groups by unit_id.
    assert "ix_diseases_unit_id" in _index_names(Disease)
