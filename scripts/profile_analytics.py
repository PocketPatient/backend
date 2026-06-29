"""
Analytics performance profiler — Week 15, Task 1.

Seeds realistic volume (200 students x 12 diagnosed cases = 2400 sessions/scores)
into the TEST database, then times the cold (uncached) service-level aggregations
that back the analytics endpoints. Cached responses are O(1) Redis reads, so this
measures the worst case the < 500ms target must hold for.

Run from backend root:
  uv run python scripts/profile_analytics.py

Leaves the test DB empty afterward (tables dropped + recreated).
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base
from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.services.analytics_service import get_class_summary, get_student_summary

URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient_test",
)

N_STUDENTS = 200
CASES_PER_STUDENT = 12
N_UNITS = 4
DISEASES_PER_UNIT = 3
CATEGORIES = ["Mood", "Anxiety", "Psychotic", "Personality"]
THRESHOLD_MS = 500


async def main() -> None:
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session_ = async_sessionmaker(engine, expire_on_commit=False)

    async with Session_() as db:
        prof = User(
            id=uuid.uuid4(), google_uid=f"p-{uuid.uuid4().hex}",
            email=f"prof-{uuid.uuid4().hex[:8]}@t.edu", role=UserRole.professor,
            is_verified=True,
        )
        db.add(prof)
        await db.flush()
        course = Course(title="Load", professor_id=prof.id, class_code="LOAD01")
        db.add(course)
        await db.flush()

        diseases = []
        for u in range(N_UNITS):
            unit = Unit(
                course_id=course.id, label=f"U{u}", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc),
            )
            db.add(unit)
            await db.flush()
            for d in range(DISEASES_PER_UNIT):
                dis = Disease(
                    unit_id=unit.id, name=f"D{u}-{d}",
                    category=CATEGORIES[(u + d) % len(CATEGORIES)],
                    key_symptoms=["x"], differentials=["y"], difficulty_tier=2,
                    speech_style="flat", nudge_behavior={},
                )
                db.add(dis)
                diseases.append(dis)
        await db.flush()

        students = []
        for s in range(N_STUDENTS):
            stu = User(
                id=uuid.uuid4(), google_uid=f"s-{uuid.uuid4().hex}",
                email=f"s{s}-{uuid.uuid4().hex[:6]}@t.edu", role=UserRole.student,
                is_verified=True,
            )
            db.add(stu)
            students.append(stu)
        await db.flush()

        base = datetime(2026, 8, 1, tzinfo=timezone.utc)
        for stu in students:
            db.add(Enrollment(user_id=stu.id, course_id=course.id))
            for c in range(CASES_PER_STUDENT):
                dis = random.choice(diseases)
                completed = base + timedelta(hours=c)
                sess = Session(
                    disease_id=dis.id, user_id=stu.id, course_id=course.id,
                    started_at=completed - timedelta(minutes=8), completed_at=completed,
                    status=SessionStatus.diagnosed, turn_count=random.randint(3, 12),
                    avg_response_latency_sec=random.uniform(30, 600),
                )
                db.add(sess)
                await db.flush()
                db.add(Score(
                    session_id=sess.id, primary_dx=dis.name, differentials=[],
                    justification="x" * 60, total_score=float(random.randint(0, 100)),
                ))
        await db.commit()

        sample_student = students[0].id

        async def timed(label, coro_factory) -> None:
            best = float("inf")
            for _ in range(3):
                t0 = time.perf_counter()
                await coro_factory()
                best = min(best, (time.perf_counter() - t0) * 1000)
            verdict = "OK" if best < THRESHOLD_MS else "SLOW"
            print(f"{label:28s} {best:7.1f} ms  ({verdict})")

        print(
            f"Seeded {N_STUDENTS} students x {CASES_PER_STUDENT} cases "
            f"= {N_STUDENTS * CASES_PER_STUDENT} diagnosed sessions\n"
        )
        await timed("class_summary (cold)", lambda: get_class_summary(course.id, db))
        await timed(
            "student_summary (cold)",
            lambda: get_student_summary(sample_student, course.id, db),
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
