from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AIConsentStatus(BaseModel):
    required_version: str = Field(description="Disclosure version the user must accept before any AI feature runs.")
    active: bool = Field(description="True if the user has accepted the required version and not revoked it.")
    accepted_version: str | None = Field(default=None, description="Version of the active acceptance, if any.")
    accepted_at: datetime | None = Field(default=None, description="When the active acceptance was recorded (UTC).")
