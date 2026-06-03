from __future__ import annotations

import asyncio
import random

from fastapi import HTTPException
from google import genai
from google.genai.types import GenerateContentConfig, ThinkingConfig

from app.config import settings
from app.models.disease import Disease

_PATIENT_NAMES = [
    "Sarah", "Michael", "Jennifer", "James", "Lisa",
    "Robert", "Emily", "David", "Maria", "Kevin",
]

_OPENING_PROMPT = "Generate your first message reaching out to a doctor for help."


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
        response = await asyncio.to_thread(
            self.client.models.generate_content,
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
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=conversation_history,
            config=self._gen_config(system_prompt),
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty response")
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
