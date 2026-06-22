from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.course import Course
from app.models.score import Score
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

_PRESSURED_NUDGE = {"frequency": "high", "tone": "urgent", "example": "please!"}
_FLAT_NUDGE = {"frequency": "low", "tone": "withdrawn", "example": "..."}


# --- a per-call test-DB session whose engine is built inside the calling loop ---
# Task entrypoints run on a fresh thread/loop (run_task_async); an async engine's
# connections are bound to the loop that created them, so each task gets its own
# engine created inside its own loop, disposed on context exit.
class _PerCallTestSession:
    def __call__(self):
        engine = create_async_engine(TEST_DATABASE_URL)
        session = async_sessionmaker(engine, expire_on_commit=False)()
        return _SessionCtx(engine, session)


class _SessionCtx:
    def __init__(self, engine, session):
        self._engine = engine
        self._session = session

    async def __aenter__(self):
        return await self._session.__aenter__()

    async def __aexit__(self, *exc):
        result = await self._session.__aexit__(*exc)
        await self._engine.dispose()
        return result


@pytest.fixture
def e2e_harness(monkeypatch):
    """Eager Celery + mocked gateway + recorded push + per-task test-DB sessions."""
    from app.celery_app import celery

    monkeypatch.setattr(celery.conf, "task_always_eager", True, raising=False)
    monkeypatch.setattr(celery.conf, "task_eager_propagates", True, raising=False)

    # Route every task's AsyncSessionLocal at the test DB (loop-safe per call).
    factory = _PerCallTestSession()
    for mod in (
        "app.tasks.bot_reply",
        "app.tasks.nudge",
        "app.tasks.case_initiation",
        "app.tasks.push_notifications",
    ):
        monkeypatch.setattr(f"{mod}.AsyncSessionLocal", factory)
    # create_new_session receives db from the caller, but session_service also
    # imports symbols; its create path uses the passed db, so no patch needed.

    # Record FCM sends instead of hitting firebase.
    pushes: list[tuple] = []
    monkeypatch.setattr(
        "app.services.push_service.send_push_notification",
        lambda token, title, body, data: pushes.append((token, title, body, data)),
    )

    # Mock the gateway. Canned text must NOT contain the disease name (would trip
    # the diagnosis-leak guardrail) or AI-break phrases. Record calls for assertions.
    calls: dict[str, list] = {"opening": [], "patient": [], "nudge": [], "grade": [], "hint": []}

    async def _opening(disease, name, age):
        calls["opening"].append(disease)
        return "Hello doctor, I have not been feeling like myself lately."

    async def _patient(disease, name, age, history):
        calls["patient"].append(disease)
        return "It has been hard to get through the day."

    async def _nudge(disease, name, age, hours):
        calls["nudge"].append((disease, hours))
        return "Are you still there, doctor?"

    grade_result = {"is_correct": False, "rubric_score": 40.0, "feedback": "keep going"}

    async def _grade(disease, submission, transcript):
        calls["grade"].append(disease)
        return dict(grade_result)

    async def _hint(wrong_dx, actual_dx):
        calls["hint"].append((wrong_dx, actual_dx))
        return "Consider the mood symptoms more closely."

    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_opening_message", _opening)
    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_patient_message", _patient)
    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_nudge_message", _nudge)
    monkeypatch.setattr("app.services.llm_gateway.gateway.grade_diagnosis", _grade)
    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_hint", _hint)

    return {"pushes": pushes, "calls": calls, "grade_result": grade_result}


async def _seed(db_session, prof, stu, *, speech_style, nudge_behavior, name):
    course = Course(
        title="E2E", professor_id=prof.id, class_code=f"E2E{uuid.uuid4().hex[:3].upper()}",
        is_active=True, msg_window_start=time(0, 0), msg_window_end=time(23, 59),
        msg_timezone="UTC",
    )
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(
        unit_id=unit.id, name=name, category="Mood", key_symptoms=["low mood"],
        differentials=["GAD"], difficulty_tier=2, speech_style=speech_style,
        nudge_behavior=nudge_behavior,
    )
    db_session.add(disease)
    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    stu.fcm_token = "device-token"
    db_session.add(stu)
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(disease)
    return course, disease


async def test_full_case_lifecycle(e2e_harness, professor, student, db_session, client):
    from app.tasks.case_initiation import initiate_case

    prof, _ = professor
    stu, token = student
    course, disease = await _seed(
        db_session, prof, stu, speech_style="flat",
        nudge_behavior=_FLAT_NUDGE, name="Major Depressive Disorder",
    )
    auth = {"Authorization": f"Bearer {token}"}

    # 1. Scheduler initiates the case (eager).
    initiate_case(str(stu.id), str(course.id))
    sess = (await db_session.execute(
        select(Session).where(Session.user_id == stu.id, Session.status == SessionStatus.active)
    )).scalar_one()
    msgs = (await db_session.execute(
        select(Message).where(Message.session_id == sess.id)
    )).scalars().all()
    assert any(m.role == MessageRole.patient for m in msgs)  # opening message
    assert any(p[3]["type"] == "new_case" for p in e2e_harness["pushes"])

    # 2-4. Student replies → eager bot reply persists a patient turn.
    for _ in range(2):
        r = await client.post(
            f"/api/v1/sessions/{sess.id}/messages?instant=true",
            json={"content": "Tell me more about how you feel."}, headers=auth,
        )
        assert r.status_code == 202
    await db_session.refresh(sess)
    patient_turns = (await db_session.execute(
        select(Message).where(
            Message.session_id == sess.id, Message.role == MessageRole.patient,
            Message.is_nudge == False,  # noqa: E712
        )
    )).scalars().all()
    assert len(patient_turns) >= 3  # opening + 2 replies
    assert sess.pending_reply_task_id is None  # cleared after each reply
    assert sess.turn_count >= 2  # turn_count starts at 0; 2 eager bot replies each increment it by 1

    # 5. Wrong diagnosis → hint, session stays active.
    r = await client.post(
        f"/api/v1/sessions/{sess.id}/diagnose",
        json={"primary_dx": "Bipolar", "differentials": [], "justification": "Patient seems to have mood swings and elevated energy levels with decreased need for sleep."},
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["correct"] is False
    assert r.json()["hint"]
    await db_session.refresh(sess)
    assert sess.status == SessionStatus.active

    # 6. Correct diagnosis → score + reveal, session diagnosed.
    e2e_harness["grade_result"]["is_correct"] = True
    r = await client.post(
        f"/api/v1/sessions/{sess.id}/diagnose",
        json={"primary_dx": "MDD", "differentials": [], "justification": "Patient reports persistent low mood, loss of interest, and difficulty getting through the day."},
        headers=auth,
    )
    body = r.json()
    assert r.status_code == 200 and body["correct"] is True
    assert body["score"] is not None and body["reveal"]["disease_name"] == disease.name
    await db_session.refresh(sess)
    assert sess.status == SessionStatus.diagnosed
    score_row = (await db_session.execute(
        select(Score).where(Score.session_id == sess.id)
    )).scalar_one_or_none()
    assert score_row is not None and score_row.is_correct is True

    # 7. Next case auto-initiates: student now has no active session.
    active = (await db_session.execute(
        select(Session).where(Session.user_id == stu.id, Session.status == SessionStatus.active)
    )).scalar_one_or_none()
    assert active is None
    initiate_case(str(stu.id), str(course.id))
    new_active = (await db_session.execute(
        select(Session).where(Session.user_id == stu.id, Session.status == SessionStatus.active)
    )).scalar_one()
    assert new_active.id != sess.id


def test_speech_style_delay_ranges_and_prompt_wiring():
    from app.services.llm_gateway import LLMGateway
    from app.services.session_service import _reply_delay_seconds, _DELAY_RANGES_SEC
    from app.tasks.nudge import _FREQUENCY_HOURS

    # Deterministic, style-dependent reply delays.
    assert _reply_delay_seconds("pressured") == 0
    lo, hi = _DELAY_RANGES_SEC["flat"]
    d = _reply_delay_seconds("flat")
    assert lo <= d <= hi and (lo, hi) == (3600, 4 * 3600)

    # Nudge cadence differs per frequency tier.
    assert _FREQUENCY_HOURS["high"] != _FREQUENCY_HOURS["low"]

    # Prompt wiring: speech_style flows into the system prompt.
    gw = LLMGateway.__new__(LLMGateway)
    disease = MagicMock(name="d", speech_style="pressured", dsm_code="F00",
                        key_symptoms=["x"])
    disease.name = "Schizophrenia"
    prompt = gw._build_system_prompt(disease, "Sarah", 40)
    assert "pressured" in prompt


async def test_nudge_after_24h_silence(e2e_harness, professor, student, db_session):
    from app.tasks.nudge import check_and_send_nudges

    prof, _ = professor
    stu, _ = student
    course, disease = await _seed(
        db_session, prof, stu, speech_style="flat",
        nudge_behavior=_FLAT_NUDGE, name="Major Depressive Disorder",
    )
    sess = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active, turn_count=1,
    )
    db_session.add(sess)
    await db_session.flush()
    # Latest message is a patient message 25h old → eligible for first nudge.
    db_session.add(Message(
        session_id=sess.id, role=MessageRole.patient, content="hello",
        sent_at=datetime.now(timezone.utc) - timedelta(hours=25), is_nudge=False,
    ))
    await db_session.commit()

    check_and_send_nudges()  # eager

    nudges = (await db_session.execute(
        select(Message).where(Message.session_id == sess.id, Message.is_nudge == True)  # noqa: E712
    )).scalars().all()
    assert len(nudges) == 1
    assert e2e_harness["calls"]["nudge"], "nudge generation was invoked"
    called_disease, _hours = e2e_harness["calls"]["nudge"][0]
    assert called_disease.nudge_behavior["tone"] == _FLAT_NUDGE["tone"]
    assert any(p[3]["type"] == "new_message" for p in e2e_harness["pushes"])
