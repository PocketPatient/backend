from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ScoreByCase(BaseModel):
    session_id: uuid.UUID
    disease_name: str
    category: str
    score: float | None
    completed_at: datetime | None


class CategoryScore(BaseModel):
    avg_score: float
    count: int


class ResponseTimePoint(BaseModel):
    case_number: int
    avg_latency_sec: float | None


class StudentSummary(BaseModel):
    total_cases: int
    completed_cases: int
    avg_score: float | None
    avg_response_time_sec: float | None
    scores_by_case: list[ScoreByCase]
    scores_by_category: dict[str, CategoryScore]
    response_time_trend: list[ResponseTimePoint]
    weak_categories: list[str]


class CompletedSessionItem(BaseModel):
    session_id: uuid.UUID
    disease_name: str
    category: str
    score: float | None
    turn_count: int
    started_at: datetime
    completed_at: datetime | None
    avg_response_latency_sec: float | None


class PaginatedSessions(BaseModel):
    items: list[CompletedSessionItem]
    total: int
    page: int
    page_size: int
