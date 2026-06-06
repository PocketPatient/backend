from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.score import Score
from app.models.session import Session
from app.schemas.session import DiagnosisCreate
from app.services.llm_gateway import gateway
from app.services.session_service import avg_student_latency, get_session_messages

# Scoring weights — promote to per-course config later.
RUBRIC_WEIGHT = 0.7
TIME_WEIGHT = 0.3

# Response-time scoring, tuned for an asynchronous messaging app.
TIME_SCORE_FULL = 100.0
TIME_SCORE_FLOOR = 50.0
TIME_SCORE_NEUTRAL = 75.0          # no latency data → neutral, not rewarded
GRACE_LATENCY_SEC = 30 * 60        # ≤ 30 min still earns full marks
FLOOR_LATENCY_SEC = 24 * 60 * 60   # ≥ 24 h floored


def compute_response_time_score(avg_latency_sec: float | None) -> float:
    """Floored linear decay from full→floor between the grace and floor windows."""
    if avg_latency_sec is None:
        return TIME_SCORE_NEUTRAL
    if avg_latency_sec <= GRACE_LATENCY_SEC:
        return TIME_SCORE_FULL
    if avg_latency_sec >= FLOOR_LATENCY_SEC:
        return TIME_SCORE_FLOOR
    span = FLOOR_LATENCY_SEC - GRACE_LATENCY_SEC
    frac = (avg_latency_sec - GRACE_LATENCY_SEC) / span
    return TIME_SCORE_FULL - frac * (TIME_SCORE_FULL - TIME_SCORE_FLOOR)


def _build_transcript(messages: list[Message]) -> str:
    lines = []
    for m in messages:
        speaker = "Student" if m.role == MessageRole.student else "Patient"
        lines.append(f"{speaker}: {m.content}")
    return "\n".join(lines)


async def grade_diagnosis(session: Session, submission: DiagnosisCreate, db: AsyncSession) -> Score:
    """Build (but do not commit) a Score for the submission, and refresh
    session.avg_response_latency_sec as a side effect. Caller owns the txn."""
    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()
    messages = await get_session_messages(session.id, db)

    avg_latency = avg_student_latency(messages)
    session.avg_response_latency_sec = avg_latency  # refresh the metric
    transcript = _build_transcript(messages)

    result = await gateway.grade_diagnosis(disease, submission, transcript)
    time_score = compute_response_time_score(avg_latency)
    rubric = result["rubric_score"]
    total = round(RUBRIC_WEIGHT * rubric + TIME_WEIGHT * time_score, 2)

    return Score(
        session_id=session.id,
        primary_dx=submission.primary_dx,
        differentials=list(submission.differentials),
        justification=submission.justification,
        is_correct=result["is_correct"],
        rubric_score=rubric,
        response_time_score=time_score,
        total_score=total,
        feedback_text=result["feedback"],
        graded_at=datetime.now(timezone.utc),
    )


async def generate_diagnosis_hint(wrong_dx: str, actual_dx: str) -> str:
    return await gateway.generate_hint(wrong_dx, actual_dx)
