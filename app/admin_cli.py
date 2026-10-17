"""Operator CLI for admin tasks that have no self-service path.

    python -m app.admin_cli make-admin <email>
    python -m app.admin_cli verify-professor <email>
    python -m app.admin_cli list-pending-professors

In production, run it as a Cloud Run job with the API image (see
docs/deploy-cloud-run.md). The account must have signed in to the app once so
its user row exists.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.services.auth_service import mark_professor_verified


async def _get_live_user(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(
            select(User).where(
                func.lower(User.email) == email.strip().lower(), User.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()


async def make_admin(db: AsyncSession, email: str) -> str:
    user = await _get_live_user(db, email)
    if user is None:
        return f"error: no active user {email!r} (sign in to the app once first)"
    user.role = UserRole.admin
    user.is_verified = True
    await db.commit()
    return f"ok: {user.email} is now admin"


async def verify_professor(db: AsyncSession, email: str) -> str:
    user = await _get_live_user(db, email)
    if user is None:
        return f"error: no active user {email!r}"
    if await mark_professor_verified(db, user.id) is None:
        return f"error: {user.email} has role {user.role.value if user.role else None!r}, not professor"
    return f"ok: {user.email} is a verified professor"


async def list_pending_professors(db: AsyncSession) -> str:
    rows = (
        await db.execute(
            select(User).where(
                User.role == UserRole.professor,
                User.is_verified.is_not(True),
                User.deleted_at.is_(None),
            ).order_by(User.created_at)
        )
    ).scalars().all()
    return "\n".join(f"{u.email}\t{u.id}\t{u.created_at:%Y-%m-%d}" for u in rows) or "(none pending)"


async def _run(args: argparse.Namespace) -> str:
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        if args.command == "make-admin":
            return await make_admin(db, args.email)
        if args.command == "verify-professor":
            return await verify_professor(db, args.email)
        return await list_pending_professors(db)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.admin_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("make-admin", "verify-professor"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("list-pending-professors")
    result = asyncio.run(_run(parser.parse_args(argv)))
    print(result)
    return 1 if result.startswith("error") else 0


if __name__ == "__main__":
    sys.exit(main())
