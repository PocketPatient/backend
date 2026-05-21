import pytest

from app.services.disease_parser import (
    ParseError,
    ParseResult,
    ParsedDisease,
    ParsedUnit,
    parse,
    parse_json,
)

VALID_JSON = """
{
  "units": [
    {
      "label": "Unit 1: Mood Disorders",
      "diseases": [
        {
          "name": "Major Depressive Disorder",
          "dsm_code": "F32.1",
          "category": "Mood Disorders",
          "key_symptoms": ["depressed mood", "anhedonia"],
          "differentials": ["Bipolar II", "Adjustment Disorder"],
          "difficulty_tier": 2,
          "speech_style": "flat",
          "nudge_behavior": {"frequency": "low", "tone": "withdrawn", "example": "I guess you're busy too"}
        }
      ]
    }
  ]
}
"""


def test_parse_json_happy_path():
    result = parse_json(VALID_JSON)
    assert result.errors == []
    assert len(result.units) == 1
    unit = result.units[0]
    assert unit.label == "Unit 1: Mood Disorders"
    assert len(unit.diseases) == 1
    disease = unit.diseases[0]
    assert disease.name == "Major Depressive Disorder"
    assert disease.dsm_code == "F32.1"
    assert disease.difficulty_tier == 2
    assert disease.key_symptoms == ["depressed mood", "anhedonia"]
    assert disease.nudge_behavior == {
        "frequency": "low",
        "tone": "withdrawn",
        "example": "I guess you're busy too",
    }


def test_parse_json_invalid_json():
    result = parse_json("{not json")
    assert result.units == []
    assert len(result.errors) == 1
    assert "invalid JSON" in result.errors[0].message


def test_parse_json_missing_required_name():
    text = """
    {"units":[{"label":"U","diseases":[
        {"dsm_code":"F1","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert any("name" in e.location and "missing" in e.message for e in result.errors)
    assert result.units[0].diseases == []


def test_parse_json_difficulty_out_of_range():
    text = """
    {"units":[{"label":"U","diseases":[
        {"name":"X","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":99,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert any("difficulty_tier" in e.location and "1 and 5" in e.message for e in result.errors)


def test_parse_json_partial_success():
    text = """
    {"units":[{"label":"U","diseases":[
        {"name":"Good","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}},
        {"name":"","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert len(result.units) == 1
    assert len(result.units[0].diseases) == 1
    assert result.units[0].diseases[0].name == "Good"
    assert len(result.errors) >= 1


def test_parse_json_empty_units_list_is_ok():
    result = parse_json('{"units": []}')
    assert result.units == []
    assert result.errors == []


def test_parse_json_dsm_code_optional():
    text = """
    {"units":[{"label":"U","diseases":[
        {"name":"X","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert result.errors == []
    assert result.units[0].diseases[0].dsm_code is None


def test_parse_dispatch_unknown_extension():
    with pytest.raises(ValueError, match="unsupported"):
        parse("file.txt", b"data")


def test_parse_dispatch_json():
    result = parse("doc.json", VALID_JSON.encode("utf-8"))
    assert result.errors == []
    assert len(result.units) == 1


def test_parse_json_root_not_dict():
    result = parse_json("[1, 2, 3]")
    assert result.units == []
    assert len(result.errors) == 1
    assert result.errors[0].location == "<root>"
    assert "object" in result.errors[0].message


def test_parse_json_dsm_code_wrong_type_is_error():
    text = """
    {"units":[{"label":"U","diseases":[
        {"name":"X","dsm_code":123,"category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert any("dsm_code" in e.location for e in result.errors)
    assert result.units[0].diseases == []
