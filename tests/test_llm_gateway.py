from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_disease(
    name: str = "Major Depressive Disorder",
    dsm_code: str | None = "296.20",
    key_symptoms: list[str] | None = None,
    speech_style: str = "flat",
):
    d = MagicMock()
    d.name = name
    d.dsm_code = dsm_code
    d.key_symptoms = key_symptoms or ["low mood", "fatigue", "anhedonia"]
    d.speech_style = speech_style
    return d


@pytest.fixture
def mock_genai():
    with patch("app.services.llm_gateway.genai") as mock:
        mock_response = MagicMock()
        mock_response.text = "I've been feeling really down lately..."
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock.Client.return_value = mock_client
        mock.types = MagicMock()
        yield mock, mock_client


@pytest.mark.asyncio
async def test_generate_opening_message_returns_text(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()
    disease = _make_disease()

    result = await gw.generate_opening_message(disease, "Sarah", 34)

    assert result == "I've been feeling really down lately..."
    mock_client.models.generate_content.assert_called_once()


@pytest.mark.asyncio
async def test_generate_opening_message_system_prompt_contains_disease(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()
    disease = _make_disease(name="Bipolar I", dsm_code="296.40", speech_style="pressured")

    await gw.generate_opening_message(disease, "James", 42)

    call_kwargs = mock_client.models.generate_content.call_args
    config = call_kwargs.kwargs["config"]
    system_prompt = config.system_instruction
    assert "Bipolar I" in system_prompt
    assert "296.40" in system_prompt
    assert "James" in system_prompt
    assert "42" in system_prompt
    assert "pressured" in system_prompt


@pytest.mark.asyncio
async def test_generate_opening_message_no_dsm_code(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()
    disease = _make_disease(dsm_code=None)

    result = await gw.generate_opening_message(disease, "Sarah", 34)

    assert result == "I've been feeling really down lately..."


@pytest.mark.asyncio
async def test_generate_patient_message_formats_history(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()
    disease = _make_disease()
    history = [
        {"role": "model", "parts": [{"text": "I've been feeling down."}]},
        {"role": "user", "parts": [{"text": "How long has this been going on?"}]},
    ]

    result = await gw.generate_patient_message(disease, "Sarah", 34, history)

    assert result == "I've been feeling really down lately..."
    call_kwargs = mock_client.models.generate_content.call_args
    assert call_kwargs.kwargs["contents"] == history


@pytest.mark.asyncio
async def test_patient_identity_deterministic():
    from app.services.llm_gateway import patient_identity

    name1, age1 = patient_identity(12345)
    name2, age2 = patient_identity(12345)

    assert name1 == name2
    assert age1 == age2
    assert isinstance(name1, str)
    assert 25 <= age1 <= 65


@pytest.mark.asyncio
async def test_patient_identity_varies_by_seed():
    from app.services.llm_gateway import patient_identity

    name1, age1 = patient_identity(111)
    name2, age2 = patient_identity(999999)

    # Pinned expected values — deterministic by seed
    assert name1 == "James"
    assert age1 == 45
    assert name2 == "James"
    assert age2 == 40
    assert (name1, age1) != (name2, age2)
