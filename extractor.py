from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "supplier_name": {
            "type": "STRING",
            "nullable": True,
        },
        "tax_registration_number": {
            "type": "STRING",
            "nullable": True,
        },
        "invoice_number": {
            "type": "STRING",
            "nullable": True,
        },
        "issue_date": {
            "type": "STRING",
            "nullable": True,
        },
        "due_date": {
            "type": "STRING",
            "nullable": True,
        },
        "line_items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "description": {
                        "type": "STRING",
                    },
                    "quantity": {
                        "type": "INTEGER",
                        "nullable": True,
                    },
                    "unit": {
                        "type": "STRING",
                        "nullable": True,
                    },
                    "unit_price": {
                        "type": "INTEGER",
                        "nullable": True,
                    },
                    "amount": {
                        "type": "INTEGER",
                    },
                    "tax_code": {
                        "type": "STRING",
                        "enum": [
                            "T10",
                            "T08",
                        ],
                    },
                },
                "required": [
                    "description",
                    "quantity",
                    "unit",
                    "unit_price",
                    "amount",
                    "tax_code",
                ],
            },
        },
        "subtotal": {
            "type": "INTEGER",
        },
        "tax_amount": {
            "type": "INTEGER",
        },
        "total_amount": {
            "type": "INTEGER",
        },
    },
    "required": [
        "supplier_name",
        "tax_registration_number",
        "invoice_number",
        "issue_date",
        "due_date",
        "line_items",
        "subtotal",
        "tax_amount",
        "total_amount",
    ],
}


SYSTEM_PROMPT = """
You are a Japanese invoice document extraction engine.

Your task is to extract structured information from the supplied
Japanese invoice PDF or image.

IMPORTANT RULES:

1. Extract ONLY information actually visible in the document.
2. Never invent missing values.
3. Return null when a field cannot be determined.
4. Preserve Japanese supplier/company names accurately.
5. Convert Japanese dates to YYYY-MM-DD.
6. Japanese era dates such as Reiwa must be converted to Gregorian dates.
7. Japanese invoice registration numbers normally have the format:
   T followed by 13 digits.
8. Monetary amounts are Japanese yen and must be returned as integers.
9. Remove commas and currency symbols from monetary values.
10. Extract each printed line-item amount exactly.
11. Do NOT calculate or repair subtotal, tax, or total.
12. tax_code must be either T10 or T08.
13. Use T10 for 10% tax and T08 for 8% tax.
14. If information is unreadable, return null.
15. If the document contains multiple pages, inspect all pages.
16. Do not use outside knowledge to invent invoice information.

The application will independently perform all arithmetic
and accounting validation.
"""


class ExtractionError(Exception):
    """Raised when invoice extraction fails."""


class InvoiceExtractor:

    def __init__(
        self,
        client: genai.Client,
        model: str,
    ) -> None:
        self.client = client
        self.model = model

    def extract(
        self,
        path: Path,
    ) -> dict[str, Any]:

        if not path.exists():
            raise ExtractionError(
                f"Invoice file does not exist: {path}"
            )

        mime_type, _ = mimetypes.guess_type(path.name)

        if not mime_type:
            raise ExtractionError(
                f"Cannot determine MIME type: {path}"
            )

        if mime_type == "application/pdf":
            return self._extract_pdf(
                path,
                mime_type,
            )

        if mime_type.startswith("image/"):
            return self._extract_image(
                path,
                mime_type,
            )

        raise ExtractionError(
            f"Unsupported invoice type: {mime_type}"
        )

    def _extract_pdf(
        self,
        path: Path,
        mime_type: str,
    ) -> dict[str, Any]:

        try:
            uploaded_file = self.client.files.upload(
                file=str(path),
                config=types.UploadFileConfig(
                    mime_type=mime_type,
                    display_name=path.name,
                ),
            )

            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    uploaded_file,
                    SYSTEM_PROMPT,
                    (
                        "\n\nExtract the complete invoice "
                        "from every page."
                    ),
                ],
                config=self._generation_config(),
            )

        except Exception as exc:
            raise ExtractionError(
                f"Gemini PDF extraction failed: {exc}"
            ) from exc

        return self._parse_response(
            response,
            path,
        )

    def _extract_image(
        self,
        path: Path,
        mime_type: str,
    ) -> dict[str, Any]:

        try:
            image_bytes = path.read_bytes()

            image_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type,
            )

            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    image_part,
                    SYSTEM_PROMPT,
                    (
                        "\n\nExtract all visible invoice "
                        "information from this image."
                    ),
                ],
                config=self._generation_config(),
            )

        except Exception as exc:
            raise ExtractionError(
                f"Gemini image extraction failed: {exc}"
            ) from exc

        return self._parse_response(
            response,
            path,
        )

    @staticmethod
    def _generation_config() -> types.GenerateContentConfig:

        return types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=EXTRACTION_SCHEMA,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

    @staticmethod
    def _parse_response(
        response: Any,
        path: Path,
    ) -> dict[str, Any]:

        text = getattr(
            response,
            "text",
            None,
        )

        if not text:
            raise ExtractionError(
                f"Gemini returned empty response for {path.name}"
            )

        try:
            result = json.loads(text)

        except json.JSONDecodeError as exc:
            raise ExtractionError(
                f"Gemini returned invalid JSON for "
                f"{path.name}: {exc}"
            ) from exc

        InvoiceExtractor._validate_shape(result)

        return result

    @staticmethod
    def _validate_shape(
        data: dict[str, Any],
    ) -> None:

        if not isinstance(data, dict):
            raise ExtractionError(
                "Gemini response must be a JSON object"
            )

        required = {
            "supplier_name",
            "tax_registration_number",
            "invoice_number",
            "issue_date",
            "due_date",
            "line_items",
            "subtotal",
            "tax_amount",
            "total_amount",
        }

        missing = required - data.keys()

        if missing:
            raise ExtractionError(
                "Extraction missing fields: "
                f"{sorted(missing)}"
            )

        if not isinstance(
            data["line_items"],
            list,
        ):
            raise ExtractionError(
                "line_items must be an array"
            )

        for index, item in enumerate(data["line_items"]):

            if not isinstance(item, dict):
                raise ExtractionError(
                    f"line_items[{index}] "
                    "must be an object"
                )

            required_line_fields = {
                "description",
                "quantity",
                "unit",
                "unit_price",
                "amount",
                "tax_code",
            }

            missing_fields = (
                required_line_fields
                - item.keys()
            )

            if missing_fields:
                raise ExtractionError(
                    f"line_items[{index}] missing "
                    f"{sorted(missing_fields)}"
                )

            if item["tax_code"] not in {
                "T10",
                "T08",
            }:
                raise ExtractionError(
                    f"Invalid tax code at line "
                    f"{index + 1}: "
                    f"{item['tax_code']}"
                )