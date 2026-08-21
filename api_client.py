from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class AccountingAPIError(Exception):
    """Raised when the accounting API cannot be reached or returns an error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.response_body = response_body


@dataclass(frozen=True)
class AccountingAPIClient:
    base_url: str
    api_key: str
    timeout: float = 20.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "base_url",
            self.base_url.rstrip("/"),
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> requests.Response:
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}))

        url = f"{self.base_url}{path}"

        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise AccountingAPIError(
                f"Accounting API request failed: {exc}"
            ) from exc

        return response

    @staticmethod
    def _json_or_text(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text

    def _raise_for_error(self, response: requests.Response) -> None:
        if response.ok:
            return

        body = self._json_or_text(response)
        error_code = None

        if isinstance(body, dict):
            # Parse nested error object from accounting_api.py envelope
            error_obj = body.get("error")
            if isinstance(error_obj, dict):
                error_code = error_obj.get("code")
            else:
                error_code = body.get("code") or body.get("error_code")

        raise AccountingAPIError(
            (
                f"Accounting API returned HTTP {response.status_code}"
                + (f" ({error_code})" if error_code else "")
            ),
            status_code=response.status_code,
            error_code=error_code,
            response_body=body,
        )

    def get_partners(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/partners")
        self._raise_for_error(response)

        body = response.json()

        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict) and isinstance(data.get("partners"), list):
                return data["partners"]
            if isinstance(body.get("partners"), list):
                return body["partners"]

        if isinstance(body, list):
            return body

        raise AccountingAPIError(
            "Unexpected /partners response format",
            response_body=body,
        )

    def get_tax_codes(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/tax-codes")
        self._raise_for_error(response)

        body = response.json()

        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict) and isinstance(data.get("tax_codes"), list):
                return data["tax_codes"]
            if isinstance(body.get("tax_codes"), list):
                return body["tax_codes"]

        return body

    def create_invoice(self, invoice: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/invoices",
            headers={
                "Content-Type": "application/json",
            },
            json=invoice,
        )

        self._raise_for_error(response)

        body = self._json_or_text(response)

        if isinstance(body, dict):
            return body

        return {"response": body}