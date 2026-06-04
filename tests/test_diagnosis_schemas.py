from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.session import DiagnosisCreate


def test_valid_diagnosis():
    d = DiagnosisCreate(
        primary_dx="Major Depressive Disorder",
        differentials=["Bipolar II", "Adjustment Disorder"],
        justification="x" * 50,
    )
    assert d.primary_dx == "Major Depressive Disorder"
    assert len(d.differentials) == 2


def test_differentials_default_empty():
    d = DiagnosisCreate(primary_dx="MDD", justification="x" * 50)
    assert d.differentials == []


def test_primary_dx_required_nonempty():
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="", justification="x" * 50)


def test_justification_min_length_50():
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="MDD", justification="idk")


def test_max_three_differentials():
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="MDD", justification="x" * 50,
                        differentials=["a", "b", "c", "d"])
