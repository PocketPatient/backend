"""
Dev seed script — creates Phase 2 test data without Firebase.

Run from backend root:
  uv run python scripts/seed.py

Idempotent: safe to run multiple times.

Accounts created:
  professor@seed.pocketpatient.dev  (role: professor)
  student1@seed.pocketpatient.dev   (role: student)
  student2@seed.pocketpatient.dev   (role: student)
  student3@seed.pocketpatient.dev   (role: student)
  student4@seed.pocketpatient.dev   (role: student)
  student5@seed.pocketpatient.dev   (role: student)

Course: "Intro to Psychiatry" / class code PSYCH2
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.course import Course
from app.models.disease import Disease
from app.models.disease_document import DiseaseDocument
from app.models.enrollment import Enrollment
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.services import disease_parser


_FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures",
)
_DISEASE_FILE = os.path.join(_FIXTURES_DIR, "sample_diseases.json")

PROFESSOR = {
    "google_uid": "seed-professor-001",
    "email": "professor@seed.pocketpatient.dev",
    "display_name": "Dr. Seed Professor",
    "role": UserRole.professor,
}

STUDENTS = [
    {
        "google_uid": f"seed-student-00{i}",
        "email": f"student{i}@seed.pocketpatient.dev",
        "display_name": f"Seed Student {i}",
        "role": UserRole.student,
    }
    for i in range(1, 6)
]

COURSE_CLASS_CODE = "PSYCH2"


async def upsert_user(db, spec: dict) -> User:
    result = await db.execute(select(User).where(User.google_uid == spec["google_uid"]))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            google_uid=spec["google_uid"],
            email=spec["email"],
            display_name=spec["display_name"],
            role=spec["role"],
            is_verified=True,
        )
        db.add(user)
        await db.flush()
        print(f"  Created user: {spec['email']}")
    else:
        print(f"  Exists:       {spec['email']}")
    return user


async def get_or_create_course(db, professor: User) -> Course:
    result = await db.execute(
        select(Course).where(Course.class_code == COURSE_CLASS_CODE)
    )
    course = result.scalar_one_or_none()
    if course is None:
        course = Course(
            title="Intro to Psychiatry",
            professor_id=professor.id,
            class_code=COURSE_CLASS_CODE,
            semester="Fall 2026",
        )
        db.add(course)
        await db.flush()
        print(f"  Created course: {course.title} ({COURSE_CLASS_CODE})")
    else:
        print(f"  Exists:  course {course.title} ({COURSE_CLASS_CODE})")
    return course


async def enroll_student(db, course: Course, student: User) -> None:
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.course_id == course.id,
            Enrollment.user_id == student.id,
        )
    )
    if result.scalar_one_or_none() is None:
        db.add(Enrollment(course_id=course.id, user_id=student.id))
        print(f"    Enrolled: {student.email}")
    else:
        print(f"    Already enrolled: {student.email}")


async def import_disease_document(db, course: Course, professor: User) -> None:
    existing_units = (
        await db.execute(select(Unit).where(Unit.course_id == course.id))
    ).scalars().all()

    if existing_units:
        print(f"  Disease data already exists ({len(existing_units)} units) — skipping import")
        return

    with open(_DISEASE_FILE, "rb") as f:
        raw = f.read()
    parse_result = disease_parser.parse("sample_diseases.json", raw)

    if parse_result.errors:
        for e in parse_result.errors:
            print(f"  Parse error at {e.location}: {e.message}")
        raise RuntimeError("Sample disease file has parse errors — fix before seeding")

    doc = DiseaseDocument(
        course_id=course.id,
        uploaded_by=professor.id,
        file_url=f"/tmp/seed-{course.id}.json",
        version=1,
    )
    db.add(doc)
    await db.flush()

    for parsed_unit in parse_result.units:
        unit = Unit(course_id=course.id, label=parsed_unit.label)
        db.add(unit)
        await db.flush()
        print(f"  Created unit: {parsed_unit.label}")

        for d in parsed_unit.diseases:
            db.add(Disease(
                unit_id=unit.id,
                name=d.name,
                dsm_code=d.dsm_code,
                category=d.category,
                key_symptoms=d.key_symptoms,
                differentials=d.differentials,
                difficulty_tier=d.difficulty_tier,
                speech_style=d.speech_style,
                nudge_behavior=d.nudge_behavior,
            ))
            print(f"    + {d.name}")

    doc.parsed_at = datetime.now(timezone.utc)
    print(f"  Imported {len(parse_result.units)} units")


async def release_unit_1(db, course: Course) -> None:
    units = (
        await db.execute(
            select(Unit)
            .where(Unit.course_id == course.id)
            .order_by(Unit.created_at)
        )
    ).scalars().all()

    if not units:
        print("  No units to release")
        return

    unit1 = units[0]
    if unit1.status == UnitStatus.released:
        print(f"  Unit already released: {unit1.label}")
    else:
        unit1.status = UnitStatus.released
        unit1.release_date = datetime.now(timezone.utc)
        print(f"  Released: {unit1.label}")


async def main() -> None:
    print("=== PocketPatient Dev Seed ===\n")

    async with AsyncSessionLocal() as db:
        print("[1/5] Creating users...")
        professor = await upsert_user(db, PROFESSOR)
        students = [await upsert_user(db, spec) for spec in STUDENTS]

        print("\n[2/5] Creating course...")
        course = await get_or_create_course(db, professor)

        print("\n[3/5] Enrolling students...")
        for student in students:
            await enroll_student(db, course, student)

        print("\n[4/5] Importing disease document...")
        await import_disease_document(db, course, professor)

        print("\n[5/5] Releasing Unit 1...")
        await release_unit_1(db, course)

        await db.commit()

    print("\n=== Seed complete ===")
    print(f"\nCourse class code: {COURSE_CLASS_CODE}")
    print("Professor:         professor@seed.pocketpatient.dev")
    print("Students:          student1-5@seed.pocketpatient.dev")


if __name__ == "__main__":
    asyncio.run(main())
