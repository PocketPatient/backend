from __future__ import annotations

from datetime import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.openapi import errors
from app.models.user import User, UserRole
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])


class RoleRequest(BaseModel):
    role: Literal["student", "professor"]


@router.get("/me", response_model=UserOut, summary="Get the current user", responses=errors(401, 429))
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me/role", response_model=UserOut, summary="Set the current user's role", responses=errors(401, 409, 422, 429))
async def set_role(
    body: RoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role is not None:
        raise HTTPException(status_code=409, detail="Role already set")
    current_user.role = UserRole(body.role)
    # A self-assigned professor is NOT verified: is_verified stays False here and
    # can only be flipped True out-of-band (auth_service.mark_professor_verified).
    # require_role("professor") blocks unverified professors, so a user cannot
    # unilaterally grant themselves professor-only access.
    current_user.is_verified = body.role == "student"
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user


class FcmTokenRequest(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=512)


@router.put("/me/fcm-token", response_model=UserOut, summary="Update the FCM push token", responses=errors(401, 422, 429))
async def register_fcm_token(
    body: FcmTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    current_user.fcm_token = body.fcm_token
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user


class NotificationPreferences(BaseModel):
    push_enabled: bool = Field(description="Whether push notifications are enabled for the user.")
    quiet_hours_start: time | None = Field(default=None, description="Local time when quiet hours begin (push suppressed). Must be set together with quiet_hours_end.")
    quiet_hours_end: time | None = Field(default=None, description="Local time when quiet hours end. Must be set together with quiet_hours_start.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "push_enabled": True,
                "quiet_hours_start": "22:00:00",
                "quiet_hours_end": "08:00:00",
            }
        }
    }

    @model_validator(mode="after")
    def _quiet_hours_both_or_neither(self) -> "NotificationPreferences":
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError(
                "quiet_hours_start and quiet_hours_end must be set together"
            )
        return self


@router.put("/me/notification-preferences", response_model=NotificationPreferences, summary="Update notification preferences", responses=errors(401, 422, 429))
async def set_notification_preferences(
    body: NotificationPreferences,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationPreferences:
    current_user.push_enabled = body.push_enabled
    current_user.quiet_hours_start = body.quiet_hours_start
    current_user.quiet_hours_end = body.quiet_hours_end
    db.add(current_user)
    await db.commit()
    return body
