"""api.services.export.csv_safe: spreadsheet formula-injection guard."""

import pytest

from api.services.export import csv_safe


@pytest.mark.parametrize("value", ["=SUM(A1)", "+1", "-2+3", "@cmd", "\tx", "\rx"])
def test_formula_prefixes_are_neutralized(value):
    assert csv_safe(value) == "'" + value


@pytest.mark.parametrize("value", ["", "prod.users", "dpo@company.com", "a=b"])
def test_regular_values_are_unchanged(value):
    assert csv_safe(value) == value
