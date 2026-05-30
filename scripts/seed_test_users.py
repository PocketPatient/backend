"""
Creates two test Firebase + DB users for local development:
  student@test.pocketpatient.dev  / TestPass123!  (role: student)
  professor@test.pocketpatient.dev / TestPass123!  (role: professor)

Run from the backend root:
  python scripts/seed_test_users.py
"""

import asyncio
import sys
import os

# Allow running from the backend root directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.user import User, UserRole

TEST_USERS = [
    {
        "email": "student@test.pocketpatient.dev",
        "password": "TestPass123!",
        "display_name": "Test Student",
        "role": UserRole.student,
    },
    {
        "email": "professor@test.pocketpatient.dev",
        "password": "TestPass123!",
        "display_name": "Test Professor",
        "role": UserRole.professor,
    },
]


def init_firebase():
    if not firebase_admin._apps:
        if settings.firebase_credentials_path:
            cred = credentials.Certificate(settings.firebase_credentials_path)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app(
                options={"projectId": settings.firebase_project_id}
            )


def get_or_create_firebase_user(email: str, password: str, display_name: str) -> str:
    """Returns the Firebase UID, creating the user if needed."""
    try:
        user = firebase_auth.get_user_by_email(email)
        print(f"  Firebase user already exists: {email} ({user.uid})")
        # Update password in case it changed
        firebase_auth.update_user(user.uid, password=password, email_verified=True)
        return user.uid
    except firebase_auth.UserNotFoundError:
        user = firebase_auth.create_user(
            email=email,
            password=password,
            display_name=display_name,
            email_verified=True,  # skip verification for test accounts
        )
        print(f"  Created Firebase user: {email} ({user.uid})")
        return user.uid


async def upsert_db_user(uid: str, email: str, display_name: str, role: UserRole):
    """Insert or update the user record in PostgreSQL."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.google_uid == uid))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                google_uid=uid,
                email=email,
                display_name=display_name,
                role=role,
                is_verified=True,
            )
            db.add(user)
            print(f"  Created DB user: {email} (role={role.value})")
        else:
            user.role = role
            user.is_verified = True
            user.display_name = display_name
            print(f"  Updated DB user: {email} (role={role.value})")
        await db.commit()


async def main():
    print("Initializing Firebase...")
    init_firebase()

    for spec in TEST_USERS:
        print(f"\nProcessing {spec['email']}...")
        uid = get_or_create_firebase_user(
            spec["email"], spec["password"], spec["display_name"]
        )
        await upsert_db_user(uid, spec["email"], spec["display_name"], spec["role"])

    print("\nDone! Test credentials:")
    print("  Student:   student@test.pocketpatient.dev  /  TestPass123!")
    print("  Professor: professor@test.pocketpatient.dev  /  TestPass123!")
    print("\nNote: these accounts bypass email verification (email_verified=True in Firebase).")


if __name__ == "__main__":
    asyncio.run(main())
