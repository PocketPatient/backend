import json

import pytest

from app.services.disease_parser import (
    ParseError,
    ParseResult,
    ParsedDisease,
    ParsedUnit,
    parse,
    parse_csv,
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


VALID_CSV = """unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example
Unit 1: Mood,Major Depressive Disorder,F32.1,Mood Disorders,depressed mood;anhedonia,Bipolar II;Adjustment Disorder,2,flat,low,withdrawn,I guess you're busy too
Unit 1: Mood,Bipolar I,F31.1,Mood Disorders,mania;elevated mood,Bipolar II;Schizoaffective,3,pressured,high,urgent,I have the best idea ever
Unit 2: Anxiety,Generalized Anxiety,F41.1,Anxiety Disorders,worry;tension,Panic;Adjustment,2,tangential,high,worried,Did you see my last message?
"""


def test_parse_csv_happy_path():
    result = parse_csv(VALID_CSV)
    assert result.errors == []
    assert len(result.units) == 2
    mood = next(u for u in result.units if u.label == "Unit 1: Mood")
    assert len(mood.diseases) == 2
    assert mood.diseases[0].key_symptoms == ["depressed mood", "anhedonia"]
    assert mood.diseases[0].nudge_behavior == {
        "frequency": "low",
        "tone": "withdrawn",
        "example": "I guess you're busy too",
    }
    anxiety = next(u for u in result.units if u.label == "Unit 2: Anxiety")
    assert len(anxiety.diseases) == 1


def test_parse_csv_missing_required_field():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        "Unit 1,,F1,Mood,a;b,c;d,1,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert any("name" in e.location for e in result.errors)
    assert result.units == [] or all(u.diseases == [] for u in result.units)


def test_parse_csv_difficulty_tier_not_int():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        "Unit 1,X,F1,Mood,a;b,c;d,not-a-number,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert any("difficulty_tier" in e.location for e in result.errors)
    assert len(result.errors) == 1


def test_parse_csv_dsm_code_blank_becomes_none():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        "Unit 1,X,,Mood,a;b,c;d,1,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert result.errors == []
    assert result.units[0].diseases[0].dsm_code is None


def test_parse_csv_missing_unit_label():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        ",X,F1,Mood,a;b,c;d,1,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert any("unit_label" in e.location for e in result.errors)


def test_parse_dispatch_csv():
    result = parse("doc.csv", VALID_CSV.encode("utf-8"))
    assert result.errors == []
    assert len(result.units) == 2


def test_parse_json_empty_string_returns_empty_result():
    result = parse_json("")
    assert result.units == []
    assert result.errors == []


def test_parse_json_whitespace_only_returns_empty_result():
    result = parse_json("   \n\t  ")
    assert result.units == []
    assert result.errors == []


def test_parse_csv_empty_string_returns_empty_result():
    result = parse_csv("")
    assert result.units == []
    assert result.errors == []


def test_parse_non_utf8_returns_parse_error():
    # Latin-1 / Windows-1252 encoded bytes with a non-ASCII byte (0xe9 = é)
    # are not valid UTF-8 and must surface as a parse error, not a 500.
    raw = '{"units": []}'.encode("utf-8").replace(b"units", b"unit\xe9")
    result = parse("doc.json", raw)
    assert result.units == []
    assert len(result.errors) == 1
    assert result.errors[0].location == "<root>"
    assert "UTF-8" in result.errors[0].message


def test_parse_utf16_returns_parse_error():
    result = parse("doc.json", VALID_JSON.encode("utf-16"))
    assert result.units == []
    assert any("UTF-8" in e.message for e in result.errors)


def test_parse_json_over_length_name_is_error():
    long_name = "X" * 256  # Disease.name is String(255)
    text = json.dumps(
        {
            "units": [
                {
                    "label": "U",
                    "diseases": [
                        {
                            "name": long_name,
                            "category": "C",
                            "key_symptoms": ["s"],
                            "differentials": ["d"],
                            "difficulty_tier": 1,
                            "speech_style": "flat",
                            "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                        }
                    ],
                }
            ]
        }
    )
    result = parse_json(text)
    assert any("name" in e.location and "maximum length" in e.message for e in result.errors)
    assert result.units[0].diseases == []


def test_parse_json_over_length_dsm_code_is_error():
    text = json.dumps(
        {
            "units": [
                {
                    "label": "U",
                    "diseases": [
                        {
                            "name": "X",
                            "dsm_code": "F" * 21,  # String(20)
                            "category": "C",
                            "key_symptoms": ["s"],
                            "differentials": ["d"],
                            "difficulty_tier": 1,
                            "speech_style": "flat",
                            "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                        }
                    ],
                }
            ]
        }
    )
    result = parse_json(text)
    assert any("dsm_code" in e.location and "maximum length" in e.message for e in result.errors)


def test_parse_json_over_length_unit_label_is_error():
    text = json.dumps({"units": [{"label": "U" * 101, "diseases": []}]})
    result = parse_json(text)
    assert any("label" in e.location and "maximum length" in e.message for e in result.errors)
    assert result.units == []


def test_parse_csv_over_length_category_is_error():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        f"Unit 1,X,F1,{'C' * 101},a;b,c;d,1,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert any("category" in e.location and "maximum length" in e.message for e in result.errors)
