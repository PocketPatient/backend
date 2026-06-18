from __future__ import annotations

import uuid

import pytest

from app.models.message import MessageRole
from app.services.character_guardrail import (
    FALLBACK_TEXT,
    check_character_break,
    generate_in_character,
)


class _FakeDB:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)


def test_check_detects_ai_break():
    assert check_character_break("Well, as an AI I can't feel that.", "MDD") == "ai_break"


def test_check_detects_diagnosis_leak():
    assert check_character_break("I think I have MDD, doctor.", "MDD") == "diagnosis_leak"


def test_check_passes_clean_text():
    assert check_character_break("I just feel so tired and sad lately.", "MDD") is None


async def test_generate_in_character_returns_clean_first_try():
    db = _FakeDB()

    async def gen():
        return "I can't sleep and I feel hopeless."

    out = await generate_in_character(
        gen, disease_name="MDD", db=db, session_id=uuid.uuid4()
    )
    assert out == "I can't sleep and I feel hopeless."
    assert db.added == []


async def test_generate_in_character_regenerates_then_succeeds():
    db = _FakeDB()
    replies = iter(["As an AI, I cannot help.", "I just feel awful, doctor."])

    async def gen():
        return next(replies)

    out = await generate_in_character(
        gen, disease_name="MDD", db=db, session_id=uuid.uuid4()
    )
    assert out == "I just feel awful, doctor."
    assert len(db.added) == 1
    assert db.added[0].role == MessageRole.system
    assert "regenerated" in db.added[0].content


async def test_generate_in_character_falls_back_after_max_retries():
    db = _FakeDB()

    async def gen():
        return "I have MDD."  # always leaks

    out = await generate_in_character(
        gen, disease_name="MDD", db=db, session_id=uuid.uuid4()
    )
    assert out == FALLBACK_TEXT
    assert len(db.added) == 3
    assert "fallback used" in db.added[-1].content
    assert all(m.role == MessageRole.system for m in db.added)
