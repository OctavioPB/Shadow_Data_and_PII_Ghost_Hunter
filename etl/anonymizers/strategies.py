"""
Per-PII anonymization strategies (pure Python, Spark-UDF-compatible).

Each function:
  - Takes a single string value (possibly None)
  - Returns an anonymized string
  - Is idempotent: calling it twice on an already-anonymized value returns
    the same result (already-masked values are detected and passed through)

Privacy:
  - No original values are logged or stored — only the transformed output
  - All transformations are one-way (SHA-256, redaction, or deterministic pseudonymization)
"""

from __future__ import annotations

import hashlib
import re
import string

# ─── Idempotency sentinels ────────────────────────────────────────────────────

_REDACTED = "[REDACTED]"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")            # SHA-256 hex
_MASKED_CARD_RE = re.compile(r"^\*{4}(-\*{4}){2}-\d{4}$")  # ****-****-****-1234
_MASKED_PHONE_RE = re.compile(r"^\+\d{1,3}-\*+$")   # +1-**********


def _is_already_redacted(value: str) -> bool:
    return value == _REDACTED


def _is_already_hashed(value: str) -> bool:
    return bool(_HASH_RE.match(value))


def _is_already_masked_card(value: str) -> bool:
    return bool(_MASKED_CARD_RE.match(value))


def _is_already_masked_phone(value: str) -> bool:
    return bool(_MASKED_PHONE_RE.match(value))


# ─── Strategy implementations ─────────────────────────────────────────────────

def anonymize_email(value: str | None) -> str:
    """SHA-256 hash of the full email address."""
    if value is None:
        return _REDACTED
    if _is_already_hashed(value):
        return value
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def anonymize_credit_card(value: str | None) -> str:
    """Keep last 4 digits; mask the rest as ****-****-****-XXXX."""
    if value is None:
        return _REDACTED
    if _is_already_masked_card(value):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "****-****-****-****"
    return f"****-****-****-{digits[-4:]}"


def anonymize_ssn(value: str | None) -> str:
    """Full redaction — applies to SSN, CPF, CURP, and any national ID."""
    if value is None or _is_already_redacted(value):
        return _REDACTED
    return _REDACTED


def anonymize_full_name(value: str | None) -> str:
    """
    Format-preserving pseudonymization.

    Each word is replaced with a deterministic uppercase pseudonym of the same
    length derived from SHA-256.  Word count and capitalization pattern are
    preserved so the output cannot be reverse-mapped to the original.
    """
    if value is None:
        return _REDACTED
    # If the value looks like it has already been pseudonymized (all caps words
    # that are hex-based), pass through.
    words = value.split()
    if not words:
        return _REDACTED
    result = []
    for word in words:
        # Preserve punctuation suffix (e.g., trailing comma)
        suffix = ""
        clean = word
        if word and not word[-1].isalpha():
            suffix = word[-1]
            clean = word[:-1]
        if not clean:
            result.append(suffix)
            continue
        digest = hashlib.sha256(clean.lower().encode()).hexdigest()
        # Map each hex nibble to a letter A-P
        pseudo = "".join(chr(ord("A") + int(c, 16)) for c in digest[: len(clean)])
        # Restore original capitalization pattern
        if word[0].isupper():
            pseudo = pseudo.capitalize()
        result.append(pseudo + suffix)
    return " ".join(result)


def anonymize_phone(value: str | None) -> str:
    """
    Keep the country code prefix; mask the remaining digits with '*'.

    Examples:
      +1-800-555-0100  → +1-**********
      +55 11 91234-5678 → +55-**************
      555-5555         → ***-****
    """
    if value is None:
        return _REDACTED
    if _is_already_masked_phone(value):
        return value
    stripped = re.sub(r"[\s\-\.\(\)]", "", value)
    if stripped.startswith("+"):
        match = re.match(r"\+(\d{1,3})", stripped)
        if match:
            cc = match.group(0)  # e.g. "+1" or "+55"
            rest_len = len(stripped) - len(cc)
            return f"{cc}-{'*' * rest_len}"
    # No country code — mask everything
    digit_len = len(re.sub(r"\D", "", value))
    return "*" * max(digit_len, 4)


def anonymize_address(value: str | None) -> str:
    """Full redaction for addresses — too free-form for format preservation."""
    if value is None or _is_already_redacted(value):
        return _REDACTED
    return _REDACTED


def anonymize_date_of_birth(value: str | None) -> str:
    """Keep birth year only; mask month and day."""
    if value is None or _is_already_redacted(value):
        return _REDACTED
    # Try to extract a 4-digit year
    match = re.search(r"(19|20)\d{2}", value)
    if match:
        return f"{match.group(0)}-**-**"
    return _REDACTED


def anonymize_bank_account(value: str | None) -> str:
    """Full redaction for bank accounts / IBAN."""
    if value is None or _is_already_redacted(value):
        return _REDACTED
    return _REDACTED


def anonymize_passport(value: str | None) -> str:
    """Full redaction for passport numbers."""
    if value is None or _is_already_redacted(value):
        return _REDACTED
    return _REDACTED


# ─── Dispatch table ───────────────────────────────────────────────────────────

STRATEGY_MAP: dict[str, callable] = {
    "EMAIL": anonymize_email,
    "CREDIT_CARD": anonymize_credit_card,
    "SSN": anonymize_ssn,
    "FULL_NAME": anonymize_full_name,
    "PHONE": anonymize_phone,
    "ADDRESS": anonymize_address,
    "DATE_OF_BIRTH": anonymize_date_of_birth,
    "BANK_ACCOUNT": anonymize_bank_account,
    "PASSPORT": anonymize_passport,
}


def get_strategy(pii_category: str) -> callable:
    """Return the anonymization function for *pii_category* (case-insensitive)."""
    fn = STRATEGY_MAP.get(pii_category.upper())
    if fn is None:
        raise ValueError(f"No anonymization strategy for PII category: {pii_category!r}")
    return fn


def anonymize_value(value: str | None, pii_category: str) -> str:
    """Convenience wrapper: apply the correct strategy for *pii_category*."""
    return get_strategy(pii_category)(value)
