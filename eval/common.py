"""Shared infrastructure for the eval suite.

Import this module FIRST in every eval script: it puts the backend root on
sys.path and chdirs there so `app.config.Settings` finds `.env` (and thus
GEMINI_API_KEY) regardless of where the script was launched from.

These scripts deliberately avoid app.main / the DB / Redis — only the LLM
gateway and the character guardrail are exercised.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = BACKEND_ROOT / "eval" / "results"

sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("eval")

# Fixed persona for all experiments — eliminates persona as a variable.
PATIENT_NAME = "Alex"
PATIENT_AGE = 28


@dataclass(frozen=True)
class EvalDisease:
    """Plain-Python stand-in matching the shape of app.models.disease.Disease.

    The gateway's prompt builder only reads name / dsm_code / key_symptoms /
    speech_style, but we mirror the full model shape for realism.
    """

    name: str
    dsm_code: str
    category: str
    key_symptoms: list[str]
    differentials: list[str]
    difficulty_tier: int
    speech_style: str
    nudge_behavior: dict[str, Any] = field(default_factory=dict)


DISEASES: list[EvalDisease] = [
    EvalDisease(
        name="Bipolar I Disorder",
        dsm_code="F31.13",
        category="Bipolar and Related Disorders",
        key_symptoms=[
            "racing thoughts",
            "decreased need for sleep",
            "grandiosity",
            "rapid pressured speech",
            "impulsive spending sprees",
            "increased goal-directed activity",
            "irritability",
        ],
        differentials=[
            "Schizoaffective Disorder",
            "Attention-Deficit/Hyperactivity Disorder",
            "Substance-Induced Mood Disorder",
        ],
        difficulty_tier=3,
        speech_style="pressured",
        nudge_behavior={
            "tone": "impatient and energetic",
            "example": "Doc?? Are you there? I have SO much more to tell you, things are MOVING",
        },
    ),
    EvalDisease(
        name="Major Depressive Disorder",
        dsm_code="F33.1",
        category="Depressive Disorders",
        key_symptoms=[
            "persistent low mood",
            "loss of interest in hobbies",
            "early-morning awakening",
            "constant fatigue",
            "feelings of worthlessness",
            "poor concentration",
            "loss of appetite",
        ],
        differentials=[
            "Persistent Depressive Disorder",
            "Bipolar II Disorder",
            "Hypothyroidism",
        ],
        difficulty_tier=2,
        speech_style="flat",
        nudge_behavior={
            "tone": "withdrawn and minimal",
            "example": "sorry to bother you again. it's fine if you're busy.",
        },
    ),
    EvalDisease(
        name="Generalized Anxiety Disorder",
        dsm_code="F41.1",
        category="Anxiety Disorders",
        key_symptoms=[
            "excessive worry about everyday things",
            "restlessness",
            "muscle tension",
            "difficulty concentrating",
            "trouble falling asleep",
            "irritability",
            "easily fatigued",
        ],
        differentials=[
            "Panic Disorder",
            "Social Anxiety Disorder",
            "Major Depressive Disorder",
        ],
        difficulty_tier=2,
        speech_style="anxious",
        nudge_behavior={
            "tone": "apologetic and worried",
            "example": "I'm so sorry to message again, I just keep thinking something is really wrong...",
        },
    ),
    EvalDisease(
        name="Schizophrenia",
        dsm_code="F20.9",
        category="Schizophrenia Spectrum and Other Psychotic Disorders",
        key_symptoms=[
            "hearing voices others don't hear",
            "belief that neighbors are spying",
            "disorganized thinking",
            "social withdrawal",
            "flat affect",
            "neglect of self-care",
        ],
        differentials=[
            "Schizoaffective Disorder",
            "Bipolar I Disorder with psychotic features",
            "Substance-Induced Psychotic Disorder",
        ],
        difficulty_tier=4,
        speech_style="disorganized",
        nudge_behavior={
            "tone": "confused and fragmented",
            "example": "did you get my message? the lines... sometimes messages don't go through, they take them",
        },
    ),
]


class RateLimiter:
    """Serializes call starts to stay under the free-tier 15 RPM (we target ~10)."""

    def __init__(self, rpm: float = 10.0) -> None:
        self._interval = 60.0 / rpm
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next_allowed - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self._interval


RATE_LIMITER = RateLimiter(rpm=10.0)

# Waits before eval-level retries. The gateway already retries transient
# APIErrors internally (1/2/4s); these longer waits are for sustained 429s.
_RETRY_WAITS = (15.0, 30.0)


async def call_with_retry(fn: Callable[[], Awaitable[str]], *, what: str) -> str | None:
    """Run `fn` (a gateway call) with rate limiting; up to 3 attempts total.

    Every failure is logged. After the final failure returns None so the
    caller can record a null and keep going.
    """
    attempts = len(_RETRY_WAITS) + 1
    for attempt in range(1, attempts + 1):
        try:
            await RATE_LIMITER.wait()
            return await fn()
        except Exception as exc:  # noqa: BLE001 — a failed call must never kill a run
            logger.warning("API failure on %s (attempt %d/%d): %s", what, attempt, attempts, exc)
            if attempt <= len(_RETRY_WAITS):
                await asyncio.sleep(_RETRY_WAITS[attempt - 1])
    logger.error("Giving up on %s after %d attempts; recording null", what, attempts)
    return None


def save_results(name: str, data: Any) -> Path:
    """Write JSON to eval/results/{name}_{timestamp}.json and return the path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{name}_{timestamp}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    logger.info("Saved results to %s", path)
    return path


def latest_results(name: str) -> Path | None:
    """Most recent eval/results/{name}_{timestamp}.json, or None."""
    pattern = re.compile(rf"^{re.escape(name)}_\d{{8}}_\d{{6}}\.json$")
    candidates = sorted(p for p in RESULTS_DIR.glob(f"{name}_*.json") if pattern.match(p.name))
    return candidates[-1] if candidates else None
