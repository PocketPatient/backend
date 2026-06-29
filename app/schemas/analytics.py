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


class UnitCompletion(BaseModel):
    unit_label: str
    total_diseases: int
    total_cases_started: int
    total_diagnosed: int
    avg_score: float | None


class ScoreBucket(BaseModel):
    range: str
    count: int


class CategoryHeatmap(BaseModel):
    students: list[str]
    categories: list[str]
    scores: list[list[float | None]]


class FlaggedStudent(BaseModel):
    email: str
    avg_score: float
    completed_cases: int


class ClassSummary(BaseModel):
    enrolled_students: int
    students_with_active_case: int
    total_completed_cases: int
    avg_class_score: float | None
    completion_by_unit: list[UnitCompletion]
    score_distribution: list[ScoreBucket]
    category_heatmap: CategoryHeatmap
    flagged_students: list[FlaggedStudent]


class StudentDrilldown(StudentSummary):
    sessions: list[CompletedSessionItem]
    total: int
