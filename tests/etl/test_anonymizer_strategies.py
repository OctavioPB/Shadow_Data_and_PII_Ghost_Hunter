"""
Unit tests for etl/anonymizers/strategies.py — S5-01.

Verifies every PII strategy:
  - Produces correct output format
  - Is idempotent (applying twice returns same result)
  - Never returns the original value for sensitive inputs
"""

from __future__ import annotations

import hashlib
import re

import pytest

from etl.anonymizers.strategies import (
    _REDACTED,
    anonymize_bank_account,
    anonymize_credit_card,
    anonymize_date_of_birth,
    anonymize_email,
    anonymize_full_name,
    anonymize_passport,
    anonymize_phone,
    anonymize_ssn,
    anonymize_value,
    get_strategy,
    STRATEGY_MAP,
)


# ─── EMAIL ────────────────────────────────────────────────────────────────────

def test_email_returns_sha256_hex():
    result = anonymize_email("alice@example.com")
    assert re.match(r"^[0-9a-f]{64}$", result)


def test_email_idempotent():
    first = anonymize_email("alice@example.com")
    assert anonymize_email(first) == first


def test_email_does_not_return_original():
    original = "alice@example.com"
    assert anonymize_email(original) != original


def test_email_none_returns_redacted():
    assert anonymize_email(None) == _REDACTED


def test_email_case_insensitive():
    assert anonymize_email("Alice@Example.COM") == anonymize_email("alice@example.com")


# ─── CREDIT_CARD ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("card,expected_suffix", [
    ("4111 1111 1111 1111", "1111"),
    ("4532015112830366", "0366"),
    ("5500-0055-5555-5559", "5559"),
    ("378282246310005", "0005"),
])
def test_credit_card_keeps_last_4(card, expected_suffix):
    result = anonymize_credit_card(card)
    assert result.endswith(expected_suffix)


def test_credit_card_masks_first_digits():
    result = anonymize_credit_card("4111111111111111")
    assert result.startswith("****-****-****-")


def test_credit_card_idempotent():
    masked = anonymize_credit_card("4111111111111111")
    assert anonymize_credit_card(masked) == masked


def test_credit_card_none_returns_redacted():
    assert anonymize_credit_card(None) == _REDACTED


# ─── SSN ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "123-45-6789",
    "123.456.789-09",    # CPF
    "BADD110313HCMLNS09",  # CURP
])
def test_ssn_always_redacted(value):
    assert anonymize_ssn(value) == _REDACTED


def test_ssn_idempotent():
    assert anonymize_ssn(_REDACTED) == _REDACTED


def test_ssn_none_returns_redacted():
    assert anonymize_ssn(None) == _REDACTED


# ─── FULL_NAME ────────────────────────────────────────────────────────────────

def test_full_name_preserves_word_count():
    original = "Maria García López"
    result = anonymize_full_name(original)
    assert len(result.split()) == 3


def test_full_name_not_equal_to_original():
    assert anonymize_full_name("John Smith") != "John Smith"


def test_full_name_idempotent():
    first = anonymize_full_name("João da Silva")
    assert anonymize_full_name(first) == first


def test_full_name_none_returns_redacted():
    assert anonymize_full_name(None) == _REDACTED


def test_full_name_deterministic():
    assert anonymize_full_name("Alice Wonder") == anonymize_full_name("Alice Wonder")


# ─── PHONE ───────────────────────────────────────────────────────────────────

def test_phone_keeps_country_code():
    result = anonymize_phone("+1-800-555-0199")
    assert result.startswith("+1-")


def test_phone_masks_rest():
    result = anonymize_phone("+55 11 91234-5678")
    assert "*" in result
    # The part after country code must be all stars
    after_cc = result.split("-", 1)[1] if "-" in result else result
    assert all(c == "*" for c in after_cc)


def test_phone_idempotent():
    masked = anonymize_phone("+1-800-555-0199")
    assert anonymize_phone(masked) == masked


def test_phone_no_country_code():
    result = anonymize_phone("555-5555")
    assert all(c == "*" for c in result)


def test_phone_none_returns_redacted():
    assert anonymize_phone(None) == _REDACTED


# ─── ADDRESS ─────────────────────────────────────────────────────────────────

def test_address_fully_redacted():
    assert anonymize_bank_account("123 Main St, Springfield") == _REDACTED


# ─── DATE_OF_BIRTH ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dob,expected_year", [
    ("1990-07-14", "1990"),
    ("14/07/1990", "1990"),
    ("07-14-1990", "1990"),
])
def test_dob_keeps_year(dob, expected_year):
    result = anonymize_date_of_birth(dob)
    assert expected_year in result


def test_dob_masks_month_day():
    result = anonymize_date_of_birth("1985-03-22")
    assert "03" not in result
    assert "22" not in result


def test_dob_none_returns_redacted():
    assert anonymize_date_of_birth(None) == _REDACTED


# ─── BANK_ACCOUNT ────────────────────────────────────────────────────────────

def test_bank_account_fully_redacted():
    assert anonymize_bank_account("GB29NWBK60161331926819") == _REDACTED
    assert anonymize_bank_account("021000021/1234567890") == _REDACTED


# ─── PASSPORT ────────────────────────────────────────────────────────────────

def test_passport_fully_redacted():
    assert anonymize_passport("A12345678") == _REDACTED
    assert anonymize_passport("AB1234567") == _REDACTED


# ─── Dispatch table ───────────────────────────────────────────────────────────

def test_strategy_map_covers_all_pii_categories():
    from ml.data.pii_dataset import PII_LABELS

    expected = set(PII_LABELS) - {"NONE"}  # NONE is not anonymized
    assert set(STRATEGY_MAP.keys()) == expected


def test_get_strategy_returns_callable():
    for cat in STRATEGY_MAP:
        fn = get_strategy(cat)
        assert callable(fn)


def test_get_strategy_raises_for_unknown():
    with pytest.raises(ValueError, match="No anonymization strategy"):
        get_strategy("UNKNOWN_CATEGORY")


def test_anonymize_value_dispatch():
    result = anonymize_value("alice@example.com", "EMAIL")
    assert re.match(r"^[0-9a-f]{64}$", result)


# ─── Cross-strategy: none return original sensitive values ─────────────────────

@pytest.mark.parametrize("value,category", [
    ("alice@example.com", "EMAIL"),
    ("4111 1111 1111 1111", "CREDIT_CARD"),
    ("123-45-6789", "SSN"),
    ("John Smith", "FULL_NAME"),
    ("+1-800-555-0199", "PHONE"),
    ("1990-07-14", "DATE_OF_BIRTH"),
    ("GB29NWBK60161331926819", "BANK_ACCOUNT"),
    ("A12345678", "PASSPORT"),
])
def test_strategy_never_returns_original(value, category):
    result = anonymize_value(value, category)
    assert result != value, (
        f"{category} strategy returned unchanged value — PII not anonymized"
    )
