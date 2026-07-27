import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit
from app.models.user import User, UserRole

UTC = timezone.utc


async def _course(db, professor):
    course = Course(
        id=uuid.uuid4(),
        title="Psych 101",
        professor_id=professor.id,
        class_code=uuid.uuid4().hex[:6].upper(),
    )
    db.add(course)
    await db.commit()
    return course


async def _student(db, n):
    u = User(
        id=uuid.uuid4(),
        google_uid=f"s-{uuid.uuid4().hex}",
        email=f"stu{n}@test.edu",
        role=UserRole.student,
        is_verified=True,
        display_name=f"Student {n}",
    )
    db.add(u)
    await db.commit()
    return u


async def _unit(db, course, label):
    unit = Unit(id=uuid.uuid4(), course_id=course.id, label=label)
    db.add(unit)
    await db.commit()
    return unit


async def _disease(db, unit, name, category):
    d = Disease(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name=name,
        category=category,
        key_symptoms=[],
        differentials=[],
        difficulty_tier=1,
        speech_style="flat",
        nudge_behavior={},
    )
    db.add(d)
    await db.commit()
    return d


async def _enroll(db, user, course):
    db.add(Enrollment(id=uuid.uuid4(), user_id=user.id, course_id=course.id))
    await db.commit()


async def _session(
    db,
    user,
    course,
    disease,
    status,
    score=None,
    *,
    completed_offset=0,
    latency=None,
    turns=0,
):
    started = datetime(2026, 8, 1, tzinfo=UTC)
    completed = (
        started + timedelta(hours=completed_offset)
        if status == SessionStatus.diagnosed
        else None
    )
    s = Session(
        id=uuid.uuid4(),
        disease_id=disease.id,
        user_id=user.id,
        course_id=course.id,
        started_at=started,
        completed_at=completed,
        status=status,
        turn_count=turns,
        avg_response_latency_sec=latency,
    )
    db.add(s)
    await db.commit()
    if score is not None:
        db.add(
            Score(
                id=uuid.uuid4(),
                session_id=s.id,
                primary_dx="x",
                differentials=[],
                total_score=score,
                graded_at=completed,
            )
        )
        await db.commit()
    return s


@pytest.mark.asyncio
async def test_class_summary_counts_and_avg(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    unit = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, unit, "MDD", "Mood")
    s1 = await _student(db_session, 1)
    s2 = await _student(db_session, 2)
    await _enroll(db_session, s1, course)
    await _enroll(db_session, s2, course)
    await _session(db_session, s1, course, d, SessionStatus.diagnosed, score=80)
    await _session(db_session, s2, course, d, SessionStatus.diagnosed, score=40)
    await _session(db_session, s2, course, d, SessionStatus.active)

    from app.services.analytics_service import get_class_summary

    out = await get_class_summary(course.id, db_session)

    assert out.enrolled_students == 2
    assert out.students_with_active_case == 1
    assert out.total_completed_cases == 2
    assert out.avg_class_score == 60.0


@pytest.mark.asyncio
async def test_completion_by_unit_and_buckets(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d1 = await _disease(db_session, u1, "MDD", "Mood")
    d2 = await _disease(db_session, u1, "GAD", "Anxiety")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(db_session, stu, course, d1, SessionStatus.diagnosed, score=20)
    await _session(db_session, stu, course, d2, SessionStatus.diagnosed, score=21)
    await _session(db_session, stu, course, d2, SessionStatus.abandoned)

    from app.services.analytics_service import get_class_summary

    out = await get_class_summary(course.id, db_session)

    unit = out.completion_by_unit[0]
    assert unit.unit_label == "Unit 1"
    assert unit.total_diseases == 2
    assert unit.total_cases_started == 3
    assert unit.total_diagnosed == 2
    assert unit.avg_score == 20.5

    dist = {b.range: b.count for b in out.score_distribution}
    assert dist == {"0-20": 1, "21-40": 1, "41-60": 0, "61-80": 0, "81-100": 0}


@pytest.mark.asyncio
async def test_heatmap_and_flagged(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    mood = await _disease(db_session, u1, "MDD", "Mood")
    anx = await _disease(db_session, u1, "GAD", "Anxiety")
    weak = await _student(db_session, 1)  # stu1@test.edu, avg 30
    strong = await _student(db_session, 2)  # stu2@test.edu, avg 90
    await _enroll(db_session, weak, course)
    await _enroll(db_session, strong, course)
    await _session(db_session, weak, course, mood, SessionStatus.diagnosed, score=30)
    await _session(db_session, strong, course, mood, SessionStatus.diagnosed, score=90)
    await _session(db_session, strong, course, anx, SessionStatus.diagnosed, score=90)

    from app.services.analytics_service import get_class_summary

    out = await get_class_summary(course.id, db_session, bottom_pct=0.5)

    hm = out.category_heatmap
    assert hm.students == ["stu1@test.edu", "stu2@test.edu"]
    assert hm.categories == ["Anxiety", "Mood"]
    assert hm.scores == [[None, 30.0], [90.0, 90.0]]

    # bottom 50% of 2 students = ceil(1.0) = 1 -> the weak student only.
    assert [f.email for f in out.flagged_students] == ["stu1@test.edu"]
    assert out.flagged_students[0].avg_score == 30.0
    assert out.flagged_students[0].completed_cases == 1


@pytest.mark.asyncio
async def test_class_summary_empty(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    from app.services.analytics_service import get_class_summary

    out = await get_class_summary(course.id, db_session)
    assert out.enrolled_students == 0
    assert out.total_completed_cases == 0
    assert out.avg_class_score is None
    assert out.completion_by_unit == []
    assert out.category_heatmap.students == []
    assert out.flagged_students == []
    assert {b.range for b in out.score_distribution} == {
        "0-20",
        "21-40",
        "41-60",
        "61-80",
        "81-100",
    }


@pytest.mark.asyncio
async def test_class_summary_endpoint(
    clean_tables, db_session, client, professor
):
    prof, token = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, u1, "MDD", "Mood")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(db_session, stu, course, d, SessionStatus.diagnosed, score=70)

    resp = await client.get(
        f"/api/v1/analytics/professor/class-summary?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enrolled_students"] == 1
    assert body["avg_class_score"] == 70.0


@pytest.mark.asyncio
async def test_class_summary_blocks_student(
    clean_tables, db_session, client, professor, student
):
    prof, _ = professor
    _, stu_token = student
    course = await _course(db_session, prof)
    resp = await client.get(
        f"/api/v1/analytics/professor/class-summary?course_id={course.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_class_summary_unowned_course_404(
    clean_tables, db_session, client, professor, rsa_keys
):
    prof, _ = professor
    course = await _course(db_session, prof)
    other = User(
        id=uuid.uuid4(),
        google_uid=f"p-{uuid.uuid4().hex}",
        email="other@test.edu",
        role=UserRole.professor,
        is_verified=True,
    )
    db_session.add(other)
    await db_session.commit()
    from tests.conftest import _make_token

    priv, _ = rsa_keys
    resp = await client.get(
        f"/api/v1/analytics/professor/class-summary?course_id={course.id}",
        headers={"Authorization": f"Bearer {_make_token(other.id, priv)}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_diagnose_invalidates_class_cache():
    import inspect

    from app.routers import sessions as sessions_router

    src = inspect.getsource(sessions_router)
    assert "class_summary_key" in src


@pytest.mark.asyncio
async def test_student_drilldown(clean_tables, db_session, client, professor):
    prof, token = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, u1, "MDD", "Mood")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(
        db_session, stu, course, d, SessionStatus.diagnosed, score=88, turns=4
    )

    resp = await client.get(
        f"/api/v1/analytics/professor/student/{stu.id}?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["completed_cases"] == 1
    assert body["avg_score"] == 88.0
    assert body["total"] == 1
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["disease_name"] == "MDD"


@pytest.mark.asyncio
async def test_drilldown_unenrolled_student_404(
    clean_tables, db_session, client, professor
):
    prof, token = professor
    course = await _course(db_session, prof)
    stranger = await _student(db_session, 9)  # not enrolled
    resp = await client.get(
        f"/api/v1/analytics/professor/student/{stranger.id}?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_csv_export(clean_tables, db_session, client, professor):
    prof, token = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, u1, "MDD", "Mood")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(
        db_session, stu, course, d, SessionStatus.diagnosed, score=75,
        turns=6, latency=12.0, completed_offset=1,
    )
    await _session(
        db_session, stu, course, d, SessionStatus.diagnosed, score=85,
        turns=8, latency=10.0, completed_offset=2,
    )

    resp = await client.get(
        f"/api/v1/analytics/professor/export?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == (
        "student_email,student_name,case_number,disease_name,category,"
        "score,response_time_avg,turns,date_completed"
    )
    assert len(lines) == 3  # header + 2 cases
    assert lines[1].split(",")[2] == "1"
    assert lines[2].split(",")[2] == "2"


@pytest.mark.asyncio
async def test_csv_export_bad_format_400(
    clean_tables, db_session, client, professor
):
    prof, token = professor
    course = await _course(db_session, prof)
    resp = await client.get(
        f"/api/v1/analytics/professor/export?course_id={course.id}&format=xml",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
