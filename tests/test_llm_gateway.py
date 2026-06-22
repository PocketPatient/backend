from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from google.genai import errors as genai_errors


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
async def test_generate_opening_message_disables_thinking_and_raises_tokens(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()

    await gw.generate_opening_message(_make_disease(), "Sarah", 34)

    config = mock_client.models.generate_content.call_args.kwargs["config"]
    assert config.max_output_tokens == 800
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_generate_patient_message_disables_thinking_and_raises_tokens(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()
    history = [{"role": "user", "parts": [{"text": "How are you?"}]}]

    await gw.generate_patient_message(_make_disease(), "Sarah", 34, history)

    config = mock_client.models.generate_content.call_args.kwargs["config"]
    assert config.max_output_tokens == 800
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_generate_opening_message_empty_response_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = None
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.generate_opening_message(_make_disease(), "Sarah", 34)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_patient_message_empty_response_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = ""
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.generate_patient_message(_make_disease(), "Sarah", 34, [])
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_opening_message_server_error_raises_502(mock_genai):
    from google.genai import errors

    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.side_effect = errors.ServerError(
        503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
    )
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.generate_opening_message(_make_disease(), "Sarah", 34)
    assert exc.value.status_code == 502
    assert "high demand" in exc.value.detail


@pytest.mark.asyncio
async def test_grade_diagnosis_server_error_raises_502(mock_genai):
    from google.genai import errors

    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.side_effect = errors.ServerError(
        503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
    )
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.grade_diagnosis(_make_disease(), _make_submission(), "t")
    assert exc.value.status_code == 502
    assert "high demand" in exc.value.detail


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


def _make_submission(primary_dx="Major Depressive Disorder",
                     differentials=None, justification="x" * 60):
    from unittest.mock import MagicMock
    s = MagicMock()
    s.primary_dx = primary_dx
    s.differentials = differentials if differentials is not None else ["Bipolar II"]
    s.justification = justification
    return s


@pytest.mark.asyncio
async def test_grade_diagnosis_parses_json(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = (
        '{"is_correct": true, "rubric_score": 88, "feedback": "Solid reasoning."}'
    )
    gw = LLMGateway()

    result = await gw.grade_diagnosis(_make_disease(), _make_submission(), "Patient: hi\nStudent: hello")

    assert result == {"is_correct": True, "rubric_score": 88.0, "feedback": "Solid reasoning."}


@pytest.mark.asyncio
async def test_grade_diagnosis_uses_json_output_and_disables_thinking(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = (
        '{"is_correct": false, "rubric_score": 40, "feedback": "x"}'
    )
    gw = LLMGateway()

    await gw.grade_diagnosis(_make_disease(), _make_submission(), "transcript")

    config = mock_client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_grade_diagnosis_clamps_rubric_score(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = (
        '{"is_correct": true, "rubric_score": 140, "feedback": "x"}'
    )
    gw = LLMGateway()

    result = await gw.grade_diagnosis(_make_disease(), _make_submission(), "t")
    assert result["rubric_score"] == 100.0


@pytest.mark.asyncio
async def test_grade_diagnosis_empty_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = ""
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.grade_diagnosis(_make_disease(), _make_submission(), "t")
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_grade_diagnosis_malformed_json_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = "not json at all"
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.grade_diagnosis(_make_disease(), _make_submission(), "t")
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_hint_returns_text(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = "Look more closely at the sleep pattern."
    gw = LLMGateway()

    result = await gw.generate_hint("Generalized Anxiety Disorder", "Major Depressive Disorder")
    assert result == "Look more closely at the sleep pattern."


@pytest.mark.asyncio
async def test_generate_hint_empty_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = None
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.generate_hint("GAD", "MDD")
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_grade_diagnosis_prompt_contains_diagnosis_details(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = (
        '{"is_correct": true, "rubric_score": 80, "feedback": "x"}'
    )
    gw = LLMGateway()
    disease = _make_disease(name="Major Depressive Disorder")
    submission = _make_submission(primary_dx="Bipolar I", differentials=["GAD"])

    await gw.grade_diagnosis(disease, submission, "Patient: I feel low")

    contents = mock_client.models.generate_content.call_args.kwargs["contents"]
    prompt = contents[0]["parts"][0]["text"]
    assert "Major Depressive Disorder" in prompt   # the actual condition
    assert "Bipolar I" in prompt                    # student's primary dx
    assert "GAD" in prompt                          # student's differential
    assert "Patient: I feel low" in prompt          # transcript


@pytest.mark.asyncio
async def test_generate_hint_prompt_warns_against_revealing(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = "Consider the sleep pattern."
    gw = LLMGateway()

    await gw.generate_hint("Generalized Anxiety Disorder", "Major Depressive Disorder")

    contents = mock_client.models.generate_content.call_args.kwargs["contents"]
    prompt = contents[0]["parts"][0]["text"]
    assert "Generalized Anxiety Disorder" in prompt   # the wrong guess
    assert "Major Depressive Disorder" in prompt      # actual, used in the "do not name" instruction
    lowered = prompt.lower()
    assert "without revealing" in lowered or "do not name" in lowered


@pytest.mark.asyncio
async def test_generate_nudge_message_returns_text(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = "Are you still there, doc?"
    gw = LLMGateway()
    disease = _make_disease()
    disease.nudge_behavior = {"frequency": "high", "tone": "anxious", "example": "Hello? Anyone?"}

    result = await gw.generate_nudge_message(disease, "Sarah", 34, 6)

    assert result == "Are you still there, doc?"
    mock_client.models.generate_content.assert_called_once()


@pytest.mark.asyncio
async def test_generate_nudge_message_prompt_contains_hours_tone_example(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()
    disease = _make_disease(speech_style="flat")
    disease.nudge_behavior = {"frequency": "low", "tone": "withdrawn", "example": "...still waiting"}

    await gw.generate_nudge_message(disease, "James", 42, 30)

    contents = mock_client.models.generate_content.call_args.kwargs["contents"]
    prompt = contents[0]["parts"][0]["text"]
    assert "30" in prompt
    assert "withdrawn" in prompt
    assert "...still waiting" in prompt

    config = mock_client.models.generate_content.call_args.kwargs["config"]
    assert "James" in config.system_instruction
    assert "flat" in config.system_instruction


@pytest.mark.asyncio
async def test_generate_nudge_message_handles_empty_example(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    gw = LLMGateway()
    disease = _make_disease()
    disease.nudge_behavior = {"frequency": "medium", "tone": "irritable", "example": ""}

    result = await gw.generate_nudge_message(disease, "Sarah", 34, 24)

    assert result == "I've been feeling really down lately..."
    contents = mock_client.models.generate_content.call_args.kwargs["contents"]
    prompt = contents[0]["parts"][0]["text"]
    assert "irritable" in prompt


@pytest.mark.asyncio
async def test_generate_nudge_message_empty_response_raises_502(mock_genai):
    from app.services.llm_gateway import LLMGateway
    from fastapi import HTTPException

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = ""
    gw = LLMGateway()
    disease = _make_disease()
    disease.nudge_behavior = {"frequency": "high", "tone": "anxious", "example": ""}

    with pytest.raises(HTTPException) as exc_info:
        await gw.generate_nudge_message(disease, "Sarah", 34, 6)
    assert exc_info.value.status_code == 502


def _api_error():
    # genai APIError needs a response_json; build a minimal one.
    return genai_errors.APIError(503, {"error": {"message": "unavailable"}})


async def test_generate_content_retries_then_succeeds(monkeypatch):
    from app.services.llm_gateway import LLMGateway

    gw = LLMGateway.__new__(LLMGateway)
    gw.client = MagicMock()
    gw.model = "gemini-2.5-flash"

    calls = {"n": 0}

    def _call(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _api_error()
        return MagicMock(text="ok")

    gw.client.models.generate_content = _call
    sleeps: list[float] = []

    async def _fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr("app.services.llm_gateway.asyncio.sleep", _fake_sleep)

    resp = await gw._generate_content(model=gw.model, contents=[], config=None)
    assert resp.text == "ok"
    assert sleeps == [1, 2]  # slept before retry #1 and #2; #3 succeeded


async def test_generate_content_raises_502_after_exhausting_retries(monkeypatch):
    from app.services.llm_gateway import LLMGateway

    gw = LLMGateway.__new__(LLMGateway)
    gw.client = MagicMock()
    gw.model = "gemini-2.5-flash"
    gw.client.models.generate_content = MagicMock(side_effect=_api_error())

    sleeps: list[float] = []

    async def _fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr("app.services.llm_gateway.asyncio.sleep", _fake_sleep)

    with pytest.raises(HTTPException) as exc:
        await gw._generate_content(model=gw.model, contents=[], config=None)
    assert exc.value.status_code == 502
    assert sleeps == [1, 2, 4]  # slept before each of 3 retries, then gave up


async def test_generate_content_logs_latency_on_success(monkeypatch):
    from app.services.llm_gateway import LLMGateway

    gw = LLMGateway.__new__(LLMGateway)
    gw.client = MagicMock()
    gw.model = "gemini-2.5-flash"
    gw.client.models.generate_content = MagicMock(return_value=MagicMock(text="ok"))

    with patch("app.services.llm_gateway.logger") as mock_log:
        await gw._generate_content(model=gw.model, contents=[], config=None)

    logged = [json.loads(c.args[0]) for c in mock_log.info.call_args_list]
    assert any(e["event"] == "llm_latency" and "llm_latency_ms" in e for e in logged)
