from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class MatchResult:
    partner_code: str | None
    score: float
    method: str
    partner: dict[str, Any] | None = None


def normalize_japanese_text(value: str | None) -> str:
    if not value:
        return ""

    value = unicodedata.normalize("NFKC", value)

    # Remove common Japanese/ASCII whitespace.
    value = re.sub(r"\s+", "", value)

    # Remove punctuation while retaining Unicode letters/numbers.
    value = "".join(
        char
        for char in value
        if unicodedata.category(char)[0] not in {"P", "S"}
    )

    return value.casefold()


def normalize_registration_number(value: str | None) -> str:
    if not value:
        return ""

    # Japanese invoice registration number normally has T + 13 digits.
    value = unicodedata.normalize("NFKC", value).upper()

    return re.sub(r"[^A-Z0-9]", "", value)


def _partner_names(partner: dict[str, Any]) -> list[str]:
    names: list[str] = []

    for key in (
        "name",
        "partner_name",
        "supplier_name",
        "company_name",
    ):
        value = partner.get(key)
        if isinstance(value, str) and value.strip():
            names.append(value)

    aliases = partner.get("aliases", [])

    if isinstance(aliases, str):
        names.append(aliases)
    elif isinstance(aliases, list):
        names.extend(
            item
            for item in aliases
            if isinstance(item, str)
        )

    return names


def _partner_registration_numbers(
    partner: dict[str, Any],
) -> list[str]:
    values: list[str] = []

    for key in (
        "registration_number",
        "tax_registration_number",
        "invoice_registration_number",
    ):
        value = partner.get(key)

        if isinstance(value, str):
            values.append(value)

    return values


def _partner_code(partner: dict[str, Any]) -> str | None:
    for key in (
        "partner_code",
        "code",
        "id",
    ):
        value = partner.get(key)

        if value is not None:
            return str(value)

    return None


def match_partner(
    supplier_name: str | None,
    registration_number: str | None,
    partners: list[dict[str, Any]],
    *,
    minimum_score: float = 0.78,
) -> MatchResult:
    extracted_registration = normalize_registration_number(
        registration_number
    )

    # 1. Registration number is authoritative.
    if extracted_registration:
        for partner in partners:
            for candidate in _partner_registration_numbers(partner):
                if (
                    extracted_registration
                    == normalize_registration_number(candidate)
                ):
                    return MatchResult(
                        partner_code=_partner_code(partner),
                        score=1.0,
                        method="registration_number",
                        partner=partner,
                    )

    normalized_supplier = normalize_japanese_text(supplier_name)

    if not normalized_supplier:
        return MatchResult(
            partner_code=None,
            score=0.0,
            method="no_supplier_name",
        )

    candidates: list[
        tuple[float, dict[str, Any], str]
    ] = []

    for partner in partners:
        for name in _partner_names(partner):
            normalized_name = normalize_japanese_text(name)

            if not normalized_name:
                continue

            if normalized_supplier == normalized_name:
                return MatchResult(
                    partner_code=_partner_code(partner),
                    score=1.0,
                    method="exact_name",
                    partner=partner,
                )

            score = SequenceMatcher(
                None,
                normalized_supplier,
                normalized_name,
            ).ratio()

            candidates.append(
                (score, partner, name)
            )

    if not candidates:
        return MatchResult(
            partner_code=None,
            score=0.0,
            method="no_candidates",
        )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    best_score, best_partner, _ = candidates[0]

    if best_score < minimum_score:
        return MatchResult(
            partner_code=None,
            score=best_score,
            method="below_threshold",
            partner=best_partner,
        )

    # Avoid silently choosing between nearly identical suppliers.
    if len(candidates) > 1:
        second_score = candidates[1][0]

        if (
            best_score - second_score < 0.05
            and second_score >= minimum_score
        ):
            return MatchResult(
                partner_code=None,
                score=best_score,
                method="ambiguous",
                partner=None,
            )

    return MatchResult(
        partner_code=_partner_code(best_partner),
        score=best_score,
        method="fuzzy_name",
        partner=best_partner,
    )