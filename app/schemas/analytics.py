from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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
    total_cases: int = Field(description="Total number of cases assigned to the student.")
    completed_cases: int = Field(description="Number of cases the student has fully diagnosed.")
    avg_score: float | None = Field(description="Mean total score across all completed cases (0–100), or null if none.")
    avg_response_time_sec: float | None = Field(description="Average time in seconds between AI message and student reply, or null if no data.")
    scores_by_case: list[ScoreByCase]
    scores_by_category: dict[str, CategoryScore]
    response_time_trend: list[ResponseTimePoint]
    weak_categories: list[str] = Field(description="Diagnostic categories where the student's average score is below threshold.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "total_cases": 10,
                "completed_cases": 7,
                "avg_score": 74.5,
                "avg_response_time_sec": 18.3,
                "scores_by_case": [
                    {
                        "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                        "disease_name": "Major Depressive Disorder",
                        "category": "Mood Disorders",
                        "score": 82.0,
                        "completed_at": "2026-10-01T15:00:00Z",
                    }
                ],
                "scores_by_category": {
                    "Mood Disorders": {"avg_score": 78.0, "count": 3}
                },
                "response_time_trend": [
                    {"case_number": 1, "avg_latency_sec": 22.1}
                ],
                "weak_categories": ["Anxiety Disorders"],
            }
        }
    }


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
    enrolled_students: int = Field(description="Total number of students enrolled in the course.")
    students_with_active_case: int = Field(description="Number of students currently in an active session.")
    total_completed_cases: int = Field(description="Aggregate count of all diagnosed sessions across the class.")
    avg_class_score: float | None = Field(description="Mean total score across all completed class sessions (0–100), or null if none.")
    completion_by_unit: list[UnitCompletion]
    score_distribution: list[ScoreBucket]
    category_heatmap: CategoryHeatmap
    flagged_students: list[FlaggedStudent] = Field(description="Students whose average score falls below the flagging threshold.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "enrolled_students": 30,
                "students_with_active_case": 5,
                "total_completed_cases": 120,
                "avg_class_score": 71.2,
                "completion_by_unit": [
                    {
                        "unit_label": "Unit 1",
                        "total_diseases": 5,
                        "total_cases_started": 28,
                        "total_diagnosed": 25,
                        "avg_score": 73.0,
                    }
                ],
                "score_distribution": [
                    {"range": "90-100", "count": 12},
                    {"range": "70-89", "count": 55},
                ],
                "category_heatmap": {
                    "students": ["alice@example.edu", "bob@example.edu"],
                    "categories": ["Mood Disorders", "Anxiety Disorders"],
                    "scores": [[85.0, 60.0], [72.0, None]],
                },
                "flagged_students": [
                    {
                        "email": "at_risk@example.edu",
                        "avg_score": 45.0,
                        "completed_cases": 3,
                    }
                ],
            }
        }
    }


class StudentDrilldown(StudentSummary):
    sessions: list[CompletedSessionItem]
    total: int
