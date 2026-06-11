from __future__ import annotations

import asyncio
import json
import random

from fastapi import HTTPException
from google import genai
from google.genai import errors as genai_errors
from google.genai.types import GenerateContentConfig, ThinkingConfig
from pydantic import BaseModel as _PydBaseModel

from app.config import settings
from app.models.disease import Disease

_PATIENT_NAMES = [
    "Sarah", "Michael", "Jennifer", "James", "Lisa",
    "Robert", "Emily", "David", "Maria", "Kevin",
]

_OPENING_PROMPT = "Generate your first message reaching out to a doctor for help."

_NUDGE_PROMPT_TEMPLATE = (
    "You are the same patient. You sent a message {hours} hours ago and the doctor "
    "hasn't replied.\n"
    "Your nudge tone is: {tone}\n"
    "Example of your nudge style: {example}\n"
    "Write a short follow-up message (1-2 sentences) that is consistent with your condition."
)


class _GradingSchema(_PydBaseModel):
    is_correct: bool
    rubric_score: float
    feedback: str


def patient_identity(session_id_int: int) -> tuple[str, int]:
    """Deterministically generate patient name + age from session UUID int."""
    rng = random.Random(session_id_int)
    name = rng.choice(_PATIENT_NAMES)
    age = rng.randint(25, 65)
    return name, age


class LLMGateway:
    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = "gemini-2.5-flash"

    def _gen_config(self, system_prompt: str) -> GenerateContentConfig:
        # gemini-2.5-flash is a thinking model; with a low token cap the
        # reasoning can consume the whole budget and leave response.text empty.
        # Disable thinking and give visible output room to breathe.
        return GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.8,
            max_output_tokens=800,
            thinking_config=ThinkingConfig(thinking_budget=0),
        )

    async def _generate_content(self, **kwargs):
        try:
            return await asyncio.to_thread(self.client.models.generate_content, **kwargs)
        except genai_errors.APIError as exc:
            raise HTTPException(
                status_code=502, detail=f"LLM provider error: {exc.message or exc.status}"
            ) from exc

    def _build_system_prompt(self, disease: Disease, patient_name: str, patient_age: int) -> str:
        symptoms = ", ".join(disease.key_symptoms) if disease.key_symptoms else "various symptoms"
        dsm_part = f" ({disease.dsm_code})" if disease.dsm_code else ""
        return (
            f"You are a patient named {patient_name}, {patient_age} years old.\n"
            f"You are experiencing {disease.name}{dsm_part}.\n"
            f"Your symptoms include: {symptoms}.\n"
            f"Speech style: {disease.speech_style}.\n\n"
            "RULES:\n"
            "- Stay in character at ALL times. Never break the fourth wall.\n"
            "- Never mention your diagnosis by name.\n"
            "- Describe your symptoms in everyday language, not clinical terms.\n"
            '- If speech_style is "pressured", speak fast, jump between topics, use run-on sentences.\n'
            '- If speech_style is "flat", give short, low-energy responses.\n'
            '- If speech_style is "tangential", drift off topic frequently.\n'
            '- If speech_style is "disorganized", use loose associations and occasional clanging.\n'
            "- You are reaching out to a doctor because you need help."
        )

    async def generate_opening_message(
        self, disease: Disease, patient_name: str, patient_age: int
    ) -> str:
        system_prompt = self._build_system_prompt(disease, patient_name, patient_age)
        contents = [{"role": "user", "parts": [{"text": _OPENING_PROMPT}]}]
        response = await self._generate_content(
            model=self.model,
            contents=contents,
            config=self._gen_config(system_prompt),
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty response")
        return response.text

    async def generate_patient_message(
        self,
        disease: Disease,
        patient_name: str,
        patient_age: int,
        conversation_history: list[dict],
    ) -> str:
        system_prompt = self._build_system_prompt(disease, patient_name, patient_age)
        response = await self._generate_content(
            model=self.model,
            contents=conversation_history,
            config=self._gen_config(system_prompt),
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty response")
        return response.text

    async def generate_nudge_message(
        self, disease: Disease, patient_name: str, patient_age: int, hours_since_last_message: int
    ) -> str:
        system_prompt = self._build_system_prompt(disease, patient_name, patient_age)
        nudge = disease.nudge_behavior
        prompt = _NUDGE_PROMPT_TEMPLATE.format(
            hours=hours_since_last_message,
            tone=nudge.get("tone", ""),
            example=nudge.get("example", ""),
        )
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        response = await self._generate_content(
            model=self.model,
            contents=contents,
            config=self._gen_config(system_prompt),
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty nudge response")
        return response.text

    def _build_grading_prompt(self, disease: Disease, submission, transcript: str) -> str:
        dsm = disease.dsm_code or "no DSM code"
        differentials = ", ".join(submission.differentials) if submission.differentials else "none"
        return (
            "You are a clinical evaluation system. The student was diagnosing a patient "
            f"with {disease.name} ({dsm}).\n\n"
            "The student's diagnosis:\n"
            f"- Primary: {submission.primary_dx}\n"
            f"- Differentials: {differentials}\n"
            f"- Justification: {submission.justification}\n\n"
            f"The conversation transcript:\n{transcript}\n\n"
            "Evaluate:\n"
            "1. Is the primary diagnosis correct? (exact match or clinically equivalent)\n"
            "2. Are any differentials correct?\n"
            "3. Quality of justification (does it reference specific symptoms from the conversation?)\n\n"
            'Respond in JSON: {"is_correct": bool, "rubric_score": 0-100, '
            '"feedback": "specific constructive feedback"}'
        )

    async def grade_diagnosis(self, disease: Disease, submission, transcript: str) -> dict:
        prompt = self._build_grading_prompt(disease, submission, transcript)
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        # response_schema steers Gemini toward well-formed JSON; json.loads(response.text)
        # below is the single source of truth for parsing. The manual clamp is needed
        # because the schema can't express the 0-100 range constraint.
        config = GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=800,
            thinking_config=ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
            response_schema=_GradingSchema,
        )
        response = await self._generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty grading response")
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(
                status_code=502, detail="LLM returned malformed grading response"
            ) from exc
        rubric = max(0.0, min(100.0, float(data.get("rubric_score", 0))))
        return {
            "is_correct": bool(data.get("is_correct", False)),
            "rubric_score": rubric,
            "feedback": str(data.get("feedback", "")),
        }

    async def generate_hint(self, wrong_dx: str, actual_dx: str) -> str:
        prompt = (
            f"The student guessed {wrong_dx}. The actual condition is {actual_dx}. "
            "Give a subtle hint that redirects the student without revealing the answer. "
            f"Do not name {actual_dx} or any obvious synonym. Keep it to one or two sentences."
        )
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        config = GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=200,
            thinking_config=ThinkingConfig(thinking_budget=0),
        )
        response = await self._generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty hint")
        return response.text


class _LazyGateway:
    """Module-level singleton proxy; constructs the real LLMGateway on first attribute access."""

    def __init__(self) -> None:
        self._instance: LLMGateway | None = None

    def __getattr__(self, name: str):  # noqa: ANN204
        # Only forward attributes that actually exist on LLMGateway.
        # This prevents accidental LLMGateway construction during introspection
        # by unittest.mock, inspect.iscoroutinefunction, etc., which probe for
        # attributes like _is_coroutine_marker, __func__, etc.
        if not hasattr(LLMGateway, name):
            raise AttributeError(name)
        if self._instance is None:
            self._instance = LLMGateway()
        return getattr(self._instance, name)


gateway = _LazyGateway()
