"""Archive (and optionally delete) sessions older than N years.

Dry-run by default: prints how many sessions would be archived. With --apply it
writes each session (plus its messages and score) to a timestamped JSONL file
under archives/ and then deletes the sessions. Messages cascade via the
sessions FK; scores cascade on session delete.

    uv run python -m scripts.archive_old_sessions            # dry run, 3 years
    uv run python -m scripts.archive_old_sessions --years 5
    uv run python -m scripts.archive_old_sessions --apply    # actually archive+delete
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.message import Message
from app.models.score import Score
from app.models.session import Session as SessionModel

ARCHIVE_DIR = Path("archives")


def cutoff_for(years: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    try:
        return now.replace(year=now.year - years)
    except ValueError:  # Feb 29 in a leap year -> Feb 28 in a non-leap target year
        return now.replace(year=now.year - years, day=28)


async def select_old_session_ids(db: AsyncSession, cutoff: datetime) -> list[uuid.UUID]:
    result = await db.execute(
        select(SessionModel.id)
        .where(SessionModel.started_at < cutoff)
        .order_by(SessionModel.started_at)
    )
    return [row[0] for row in result.all()]


def _serialize(obj) -> dict:
    return {
        c.name: (str(v) if isinstance(v := getattr(obj, c.name), (uuid.UUID, datetime)) else v)
        for c in obj.__table__.columns
    }


async def _export(db: AsyncSession, session_ids: list[uuid.UUID], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for sid in session_ids:
            session = await db.get(SessionModel, sid)
            messages = (await db.execute(select(Message).where(Message.session_id == sid))).scalars().all()
            score = (await db.execute(select(Score).where(Score.session_id == sid))).scalar_one_or_none()
            fh.write(json.dumps({
                "session": _serialize(session),
                "messages": [_serialize(m) for m in messages],
                "score": _serialize(score) if score else None,
            }) + "\n")


async def run(years: int, apply: bool) -> None:
    cutoff = cutoff_for(years)
    async with AsyncSessionLocal() as db:
        ids = await select_old_session_ids(db, cutoff)
        print(f"{len(ids)} sessions started before {cutoff.isoformat()}")
        if not apply:
            print("dry run — pass --apply to archive and delete")
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = ARCHIVE_DIR / f"sessions-{stamp}.jsonl"
        await _export(db, ids, out)
        for sid in ids:
            await db.delete(await db.get(SessionModel, sid))
        await db.commit()
        print(f"archived {len(ids)} sessions to {out} and deleted them")


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive sessions older than N years.")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--apply", action="store_true", help="archive AND delete (default: dry run)")
    args = parser.parse_args()
    asyncio.run(run(args.years, args.apply))


if __name__ == "__main__":
    main()
