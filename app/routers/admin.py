"""Admin-only moderation queue for AI response reports.

Admins are users with role=admin, granted out-of-band (no self-service path).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models.ai_report import AIReportStatus, AIResponseReport
from app.models.message import Message
from app.models.user import User
from app.openapi import errors
from app.schemas.ai_report import AIReportAdminOut, AIReportResolve

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin_out(report: AIResponseReport, content: str) -> AIReportAdminOut:
    return AIReportAdminOut(
        id=report.id,
        message_id=report.message_id,
        session_id=report.session_id,
        reason=report.reason,
        status=report.status,
        created_at=report.created_at,
        reporter_id=report.reporter_id,
        comment=report.comment,
        message_content=content,
        resolved_at=report.resolved_at,
        resolved_by=report.resolved_by,
        resolution_note=report.resolution_note,
    )


@router.get("/ai-reports", response_model=list[AIReportAdminOut], summary="List AI response reports (admin)", responses=errors(401, 403, 429))
async def list_ai_reports(
    status: AIReportStatus = AIReportStatus.open,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> list[AIReportAdminOut]:
    rows = (
        await db.execute(
            select(AIResponseReport, Message.content)
            .join(Message, Message.id == AIResponseReport.message_id)
            .where(AIResponseReport.status == status)
            .order_by(AIResponseReport.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [_admin_out(report, content) for report, content in rows]


@router.post("/ai-reports/{report_id}/resolve", response_model=AIReportAdminOut, summary="Resolve an AI response report (admin)", responses=errors(401, 403, 404, 422, 429))
async def resolve_ai_report(
    report_id: uuid.UUID,
    body: AIReportResolve,
    admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> AIReportAdminOut:
    row = (
        await db.execute(
            select(AIResponseReport, Message.content)
            .join(Message, Message.id == AIResponseReport.message_id)
            .where(AIResponseReport.id == report_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    report, content = row
    report.status = AIReportStatus.resolved
    report.resolved_at = datetime.now(timezone.utc)
    report.resolved_by = admin.id
    report.resolution_note = body.resolution_note
    await db.commit()
    await db.refresh(report)
    return _admin_out(report, content)
