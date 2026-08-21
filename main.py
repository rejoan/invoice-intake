from __future__ import annotations

import argparse
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai

from api_client import (
    AccountingAPIClient,
    AccountingAPIError,
)
from extractor import (
    ExtractionError,
    InvoiceExtractor,
)
from matcher import (
    match_partner,
)


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
}


TAX_RATES = {
    "T10": 0.10,
    "T08": 0.08,
}


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]


def configure_logging() -> None:
    Path("logs").mkdir(
        parents=True,
        exist_ok=True,
    )

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                "logs/invoice_intake.log",
                encoding="utf-8",
            ),
        ],
    )


def parse_date(
    value: Any,
    field_name: str,
) -> date:
    if not isinstance(
        value,
        str,
    ) or not value:
        raise ValueError(
            f"{field_name} is missing"
        )

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be YYYY-MM-DD: "
            f"{value}"
        ) from exc


def validate_invoice(
    invoice: dict[str, Any],
    valid_tax_codes: set[str],
) -> ValidationResult:
    errors: list[str] = []

    line_items = (
        invoice.get("line_items")
        or invoice.get("lines")
        or []
    )

    if not line_items:
        errors.append(
            "Invoice contains no line items"
        )

    subtotal_from_lines = 0
    tax_bases = {code: 0 for code in TAX_RATES}

    for index, item in enumerate(line_items):
        amount = item.get("amount")
        tax_code = item.get("tax_code")

        if isinstance(tax_code, dict):
            tax_code = tax_code.get("tax_code")

        if not isinstance(
            amount,
            int,
        ):
            errors.append(
                f"Line {index + 1}: "
                "amount must be integer"
            )
            continue

        if amount < 0:
            errors.append(
                f"Line {index + 1}: "
                "amount cannot be negative"
            )

        if tax_code not in valid_tax_codes:
            errors.append(
                f"Line {index + 1}: "
                f"invalid tax code {tax_code}"
            )
            continue

        subtotal_from_lines += amount
        tax_bases[tax_code] = tax_bases.get(tax_code, 0) + amount

    supplied_subtotal = invoice.get("subtotal")

    if supplied_subtotal != subtotal_from_lines:
        errors.append(
            "SUBTOTAL_MISMATCH: "
            f"invoice={supplied_subtotal}, "
            f"calculated={subtotal_from_lines}"
        )

    calculated_tax = 0

    for tax_code, base in tax_bases.items():
        if base == 0:
            continue
        calculated_tax += math.floor(
            base * TAX_RATES[tax_code]
        )

    supplied_tax = invoice.get("tax_amount")

    if supplied_tax != calculated_tax:
        errors.append(
            "TAX_MISMATCH: "
            f"invoice={supplied_tax}, "
            f"calculated={calculated_tax}"
        )

    calculated_total = (
        subtotal_from_lines
        + calculated_tax
    )

    supplied_total = invoice.get("total_amount")

    if supplied_total != calculated_total:
        errors.append(
            "TOTAL_MISMATCH: "
            f"invoice={supplied_total}, "
            f"calculated={calculated_total}"
        )

    try:
        issue_date = parse_date(
            invoice.get("issue_date"),
            "issue_date",
        )
        due_date = parse_date(
            invoice.get("due_date"),
            "due_date",
        )

        if due_date < issue_date:
            errors.append(
                "DATE_INVALID: "
                "due_date is earlier than issue_date"
            )
    except ValueError as exc:
        errors.append(str(exc))

    if not invoice.get("invoice_number"):
        errors.append("Invoice number is missing")

    if not invoice.get("supplier_name"):
        errors.append("Supplier name is missing")

    return ValidationResult(
        valid=not errors,
        errors=errors,
    )


def write_manual_review(
    path: Path,
    reason: str,
    extracted: dict[str, Any] | None = None,
) -> None:
    directory = Path("manual_review")
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = directory / f"{path.stem}.json"

    payload = {
        "source_file": str(path),
        "reason": reason,
        "extracted": extracted,
    }

    output.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def build_submission_payload(
    extracted: dict[str, Any],
    partner_code: str,
) -> dict[str, Any]:
    raw_lines = (
        extracted.get("line_items")
        or extracted.get("lines")
        or []
    )

    formatted_lines = []
    for item in raw_lines:
        formatted_lines.append({
            "description": item.get("description", "Item"),
            "quantity": item.get("quantity"),
            "unit": item.get("unit", "個"),
            "unit_price": item.get("unit_price"),
            "amount": item.get("amount", 0),
            "tax_code": item.get("tax_code", "T10"),
        })

    return {
        "partner_code": partner_code,
        "invoice_number": extracted.get("invoice_number"),
        "issue_date": extracted.get("issue_date"),
        "due_date": extracted.get("due_date"),
        "lines": formatted_lines,
        "subtotal": extracted.get("subtotal"),
        "tax_amount": extracted.get("tax_amount"),
        "total_amount": extracted.get("total_amount"),
    }


def process_invoice(
    path: Path,
    extractor: InvoiceExtractor,
    api: AccountingAPIClient,
    partners: list[dict[str, Any]],
    valid_tax_codes: set[str],
) -> bool:
    logging.info("Processing: %s", path.name)

    try:
        extracted = extractor.extract(path)
    except (ExtractionError, OSError) as exc:
        logging.error(
            "%s: extraction failed: %s",
            path.name,
            exc,
        )
        write_manual_review(
            path,
            f"EXTRACTION_FAILED: {exc}",
        )
        return False

    validation = validate_invoice(
        extracted,
        valid_tax_codes,
    )

    if not validation.valid:
        for error in validation.errors:
            logging.error(
                "%s: %s",
                path.name,
                error,
            )
        write_manual_review(
            path,
            "VALIDATION_FAILED",
            extracted,
        )
        return False

    match = match_partner(
        extracted.get("supplier_name"),
        extracted.get("tax_registration_number"),
        partners,
    )

    if not match.partner_code:
        logging.error(
            "%s: partner matching failed method=%s score=%.3f",
            path.name,
            match.method,
            match.score,
        )
        write_manual_review(
            path,
            (
                "PARTNER_NOT_FOUND_OR_AMBIGUOUS: "
                f"method={match.method}, "
                f"score={match.score:.3f}"
            ),
            extracted,
        )
        return False

    logging.info(
        "%s: partner=%s method=%s score=%.3f",
        path.name,
        match.partner_code,
        match.method,
        match.score,
    )

    payload = build_submission_payload(
        extracted,
        match.partner_code,
    )

    try:
        response = api.create_invoice(payload)
    except AccountingAPIError as exc:
        logging.error(
            "%s: API error HTTP=%s code=%s body=%s",
            path.name,
            exc.status_code,
            exc.error_code,
            exc.response_body,
        )
        write_manual_review(
            path,
            (
                "API_REGISTRATION_FAILED: "
                f"HTTP={exc.status_code}, "
                f"code={exc.error_code}"
            ),
            extracted,
        )
        return False

    logging.info(
        "%s: registered successfully: %s",
        path.name,
        response,
    )

    return True


def load_config() -> dict[str, str]:
    load_dotenv()

    gemini_api_key = os.getenv("GEMINI_API_KEY")

    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    return {
        "gemini_api_key": gemini_api_key,
        "gemini_model": os.getenv(
            "GEMINI_MODEL",
            "gemini-3.7-flash",
        ),
        "api_base_url": os.getenv(
            "ACCOUNTING_API_BASE_URL",
            "http://localhost:8080",
        ),
        "api_key": os.getenv(
            "ACCOUNTING_API_KEY",
            "demo-key-1234",
        ),
        "invoice_dir": os.getenv(
            "INVOICE_DIR",
            "invoices",
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Japanese invoice intake pipeline"
    )

    parser.add_argument(
        "--invoice-dir",
        default=None,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract, validate and match without POSTing invoices",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of invoices to process",
    )

    parser.add_argument(
        "--file",
        default=None,
        help="Process a specific invoice file name (e.g., invoice_01.pdf)",
    )

    return parser.parse_args()


def main() -> int:
    configure_logging()

    args = parse_args()

    try:
        config = load_config()
    except RuntimeError as exc:
        logging.error(str(exc))
        return 2

    invoice_dir = Path(
        args.invoice_dir or config["invoice_dir"]
    )

    if not invoice_dir.exists():
        logging.error(
            "Invoice directory does not exist: %s",
            invoice_dir,
        )
        return 2

    api = AccountingAPIClient(
        base_url=config["api_base_url"],
        api_key=config["api_key"],
    )

    # ---------------------------------------------------------
    # Load accounting master data
    # ---------------------------------------------------------

    try:
        partners = api.get_partners()
        tax_codes_data = api.get_tax_codes()
    except AccountingAPIError as exc:
        logging.error(
            "Master data loading failed: %s",
            exc,
        )
        return 2

    valid_tax_codes = set()
    if isinstance(tax_codes_data, list):
        for item in tax_codes_data:
            code = item.get("tax_code") if isinstance(item, dict) else item
            if code in TAX_RATES:
                valid_tax_codes.add(code)
    elif isinstance(tax_codes_data, dict):
        valid_tax_codes = {c for c in tax_codes_data.keys() if c in TAX_RATES}

    if not valid_tax_codes:
        logging.error("No supported tax codes returned")
        return 2

    logging.info("Loaded %d partners", len(partners))
    logging.info("Supported tax codes: %s", sorted(valid_tax_codes))

    # ---------------------------------------------------------
    # Gemini Setup
    # ---------------------------------------------------------

    gemini_client = genai.Client(
        api_key=config["gemini_api_key"]
    )

    extractor = InvoiceExtractor(
        client=gemini_client,
        model=config["gemini_model"],
    )

    logging.info("Gemini model: %s", config["gemini_model"])

    # ---------------------------------------------------------
    # Invoice files
    # ---------------------------------------------------------

    files = sorted(
        path
        for path in invoice_dir.iterdir()
        if (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    )

    if args.file:
        files = [f for f in files if f.name == args.file]
        if not files:
            logging.error("Specified file not found in %s: %s", invoice_dir, args.file)
            return 2

    if args.limit and args.limit > 0:
        files = files[:args.limit]

    if not files:
        logging.warning("No invoices found in %s", invoice_dir)
        return 0

    success = 0
    failed = 0

    for path in files:
        if args.dry_run:
            logging.info("DRY RUN: %s", path.name)

            try:
                extracted = extractor.extract(path)
                validation = validate_invoice(
                    extracted,
                    valid_tax_codes,
                )

                if not validation.valid:
                    logging.error(
                        "%s: %s",
                        path.name,
                        validation.errors,
                    )
                    failed += 1
                    continue

                match = match_partner(
                    extracted.get("supplier_name"),
                    extracted.get("tax_registration_number"),
                    partners,
                )

                if not match.partner_code:
                    logging.error(
                        "%s: partner not found",
                        path.name,
                    )
                    failed += 1
                    continue

                logging.info(
                    "%s: DRY RUN OK partner=%s invoice=%s total=%s",
                    path.name,
                    match.partner_code,
                    extracted.get("invoice_number"),
                    extracted.get("total_amount"),
                )
                success += 1

            except Exception as exc:
                logging.exception(
                    "%s: dry run failed: %s",
                    path.name,
                    exc,
                )
                failed += 1

            continue

        if process_invoice(
            path=path,
            extractor=extractor,
            api=api,
            partners=partners,
            valid_tax_codes=valid_tax_codes,
        ):
            success += 1
        else:
            failed += 1

    logging.info(
        "Finished: total=%d success=%d manual_review=%d",
        len(files),
        success,
        failed,
    )

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())