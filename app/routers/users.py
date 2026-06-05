from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User, UserRole
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])


class RoleRequest(BaseModel):
    role: Literal["student", "professor"]


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me/role", response_model=UserOut)
async def set_role(
    body: RoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role is not None:
        raise HTTPException(status_code=409, detail="Role already set")
    current_user.role = UserRole(body.role)
    current_user.is_verified = body.role == "student"
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user


class FcmTokenRequest(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=512)


@router.put("/me/fcm-token", response_model=UserOut)
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
