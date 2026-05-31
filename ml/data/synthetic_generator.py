"""
Synthetic PII dataset generator.

Produces 10,000+ labeled samples per PII category across three locales
(en_US, es_MX, pt_BR) and writes JSONL to ml/data/labeled/.

Output format (one JSON object per line):
    {"column_name": str, "value": str, "label": str}

Usage:
    python -m ml.data.synthetic_generator --out-dir ml/data/labeled --samples 10000
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import string
from pathlib import Path
from typing import Callable

from faker import Faker

# ─── Locales ─────────────────────────────────────────────────────────────────

_FAKERS: dict[str, Faker] = {
    "en_US": Faker("en_US"),
    "es_MX": Faker("es_MX"),
    "pt_BR": Faker("pt_BR"),
}

for _fk in _FAKERS.values():
    _fk.seed_instance(42)

_LOCALES = list(_FAKERS.keys())


def _fk(locale: str | None = None) -> Faker:
    if locale is None:
        locale = random.choice(_LOCALES)
    return _FAKERS[locale]


# ─── Column name pools ────────────────────────────────────────────────────────

_COLUMN_NAMES: dict[str, list[str]] = {
    "SSN": [
        "ssn", "social_security_number", "social_security_no", "ss_number",
        "num_seguro_social", "numero_seguro_social", "cpf_equivalent",
        "sin", "tax_id_us",
    ],
    "CREDIT_CARD": [
        "credit_card", "card_number", "cc_number", "credit_card_number",
        "payment_card", "card_no", "numero_tarjeta", "numero_cartao",
        "card_num", "pan",
    ],
    "EMAIL": [
        "email", "email_address", "correo", "correo_electronico",
        "e_mail", "email_addr", "user_email", "contact_email",
        "endereço_email", "email_contato",
    ],
    "PHONE": [
        "phone", "phone_number", "telefone", "telefono", "cel", "celular",
        "mobile", "mobile_number", "contact_phone", "numero_telefone",
        "fone", "phone_no",
    ],
    "FULL_NAME": [
        "full_name", "name", "customer_name", "nome", "nombre", "nombre_completo",
        "nome_completo", "person_name", "client_name", "user_name",
        "display_name", "legal_name",
    ],
    "DATE_OF_BIRTH": [
        "date_of_birth", "dob", "birth_date", "birthdate", "fecha_nacimiento",
        "data_nascimento", "birthday", "born_on", "dt_nasc", "fecha_de_nacimiento",
        "dt_nascimento",
    ],
    "ADDRESS": [
        "address", "street_address", "mailing_address", "home_address",
        "direccion", "endereço", "endereco", "residential_address",
        "billing_address", "shipping_address", "domicilio",
    ],
    "BANK_ACCOUNT": [
        "bank_account", "account_number", "iban", "bank_account_number",
        "conta_bancaria", "numero_cuenta", "conta_corrente", "account_no",
        "bank_acc", "routing_account",
    ],
    "PASSPORT": [
        "passport", "passport_number", "passport_no", "numero_pasaporte",
        "numero_passaporte", "passport_id", "travel_document",
        "passport_num", "doc_viaje",
    ],
    "NONE": [
        "id", "product_id", "order_id", "quantity", "price", "status",
        "created_at", "updated_at", "category", "sku", "score",
        "rating", "count", "amount", "description", "notes", "code",
        "region", "country_code", "currency", "tax_rate", "weight",
    ],
}


# ─── Value generators ─────────────────────────────────────────────────────────

def _gen_ssn(locale: str) -> str:
    fk = _fk(locale)
    if locale == "en_US":
        return fk.ssn()
    if locale == "es_MX":
        # Mexican CURP-like pattern (18 chars alphanumeric)
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        digits = "0123456789"
        return (
            "".join(random.choices(letters, k=4))
            + "".join(random.choices(digits, k=6))
            + "".join(random.choices(letters + digits, k=8))
        )
    # Brazilian CPF: XXX.XXX.XXX-XX
    return fk.cpf()


def _luhn_checksum(number: str) -> int:
    total = 0
    reverse_digits = number[::-1]
    for i, ch in enumerate(reverse_digits):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10


def _gen_credit_card(_locale: str) -> str:
    prefixes = ["4", "5", "37", "6011", "3528", "3589"]
    prefix = random.choice(prefixes)
    length = 15 if prefix == "37" else 16
    body = prefix + "".join(random.choices("0123456789", k=length - len(prefix) - 1))
    check = (10 - _luhn_checksum(body + "0")) % 10
    full = body + str(check)
    # Format with spaces or dashes randomly
    sep = random.choice([" ", "-", ""])
    if sep:
        return sep.join([full[i : i + 4] for i in range(0, len(full), 4)])
    return full


def _gen_email(locale: str) -> str:
    return _fk(locale).email()


def _gen_phone(locale: str) -> str:
    fk = _fk(locale)
    if locale == "pt_BR":
        return fk.cellphone_number()
    return fk.phone_number()


def _gen_full_name(locale: str) -> str:
    return _fk(locale).name()


def _gen_dob(locale: str) -> str:
    fk = _fk(locale)
    dt = fk.date_of_birth(minimum_age=18, maximum_age=90)
    fmt = random.choice(["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"])
    return dt.strftime(fmt)


def _gen_address(locale: str) -> str:
    return _fk(locale).address().replace("\n", ", ")


def _gen_bank_account(locale: str) -> str:
    fk = _fk(locale)
    if locale == "pt_BR":
        # Brazilian: agency-account format
        agency = "".join(random.choices("0123456789", k=4))
        account = "".join(random.choices("0123456789", k=7))
        return f"{agency}-{account}"
    if locale == "es_MX":
        # Mexican CLABE: 18 digits
        return "".join(random.choices("0123456789", k=18))
    # US routing + account
    routing = "".join(random.choices("0123456789", k=9))
    account = "".join(random.choices("0123456789", k=10))
    return f"{routing}/{account}"


def _gen_passport(locale: str) -> str:
    letters = string.ascii_uppercase
    digits = string.digits
    if locale == "en_US":
        return "".join(random.choices(letters, k=1)) + "".join(random.choices(digits, k=8))
    if locale == "es_MX":
        return "".join(random.choices(letters, k=2)) + "".join(random.choices(digits, k=7))
    # pt_BR: SS + 7 digits
    return "".join(random.choices(letters, k=2)) + "".join(random.choices(digits, k=7))


def _gen_none(locale: str) -> str:
    fk = _fk(locale)
    choices: list[Callable[[], str]] = [
        lambda: str(random.randint(1, 1_000_000)),
        lambda: str(round(random.uniform(0.01, 9999.99), 2)),
        lambda: fk.word(),
        lambda: random.choice(["active", "inactive", "pending", "ativo", "inativo", "activo"]),
        lambda: str(random.randint(1, 100)),
        lambda: fk.date_time_this_decade().isoformat(),
        lambda: random.choice(["USD", "BRL", "MXN", "EUR"]),
        lambda: "".join(random.choices(string.ascii_uppercase, k=3))
              + "".join(random.choices(string.digits, k=6)),
    ]
    return random.choice(choices)()


_GENERATORS: dict[str, Callable[[str], str]] = {
    "SSN": _gen_ssn,
    "CREDIT_CARD": _gen_credit_card,
    "EMAIL": _gen_email,
    "PHONE": _gen_phone,
    "FULL_NAME": _gen_full_name,
    "DATE_OF_BIRTH": _gen_dob,
    "ADDRESS": _gen_address,
    "BANK_ACCOUNT": _gen_bank_account,
    "PASSPORT": _gen_passport,
    "NONE": _gen_none,
}


# ─── Core generator ───────────────────────────────────────────────────────────

def generate_samples(
    label: str,
    n: int = 10_000,
) -> list[dict[str, str]]:
    """Return *n* labeled samples for *label*, balanced across locales."""
    gen = _GENERATORS[label]
    col_pool = _COLUMN_NAMES[label]
    samples: list[dict[str, str]] = []
    locales_cycle = _LOCALES * (n // len(_LOCALES) + 1)

    for i in range(n):
        locale = locales_cycle[i]
        try:
            value = gen(locale)
        except Exception:
            value = gen("en_US")
        samples.append(
            {
                "column_name": random.choice(col_pool),
                "value": str(value),
                "label": label,
            }
        )
    return samples


def generate_dataset(samples_per_label: int = 10_000) -> list[dict[str, str]]:
    """Generate a balanced dataset across all 10 PII categories."""
    all_samples: list[dict[str, str]] = []
    for label in _GENERATORS:
        all_samples.extend(generate_samples(label, samples_per_label))
    random.shuffle(all_samples)
    return all_samples


def write_jsonl(samples: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in samples:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic PII training data")
    parser.add_argument("--out-dir", default="ml/data/labeled", help="Output directory")
    parser.add_argument("--samples", type=int, default=10_000, help="Samples per label")
    parser.add_argument(
        "--split",
        action="store_true",
        help="Write train/val/test splits (70/15/15) in addition to full.jsonl",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating {args.samples:,} samples × {len(_GENERATORS)} labels …")
    dataset = generate_dataset(args.samples)

    full_path = out_dir / "full.jsonl"
    write_jsonl(dataset, full_path)
    print(f"  Wrote {len(dataset):,} rows → {full_path}")

    if args.split:
        n = len(dataset)
        train_end = int(n * 0.70)
        val_end = train_end + int(n * 0.15)

        for name, chunk in [
            ("train", dataset[:train_end]),
            ("val", dataset[train_end:val_end]),
            ("test", dataset[val_end:]),
        ]:
            p = out_dir / f"{name}.jsonl"
            write_jsonl(chunk, p)
            print(f"  Wrote {len(chunk):,} rows → {p}")

    print("Done.")


if __name__ == "__main__":
    main()
