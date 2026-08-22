# Automated Invoice Intake Pipeline

## 1. Overview

This project implements an automated Japanese invoice intake pipeline that
processes PDF and image-based invoices, extracts structured invoice
information using Google's Gemini multimodal AI, validates accounting
rules locally, matches the supplier against a partner master, and
registers the invoice with the mock accounting system API.

The pipeline separates:

- Document understanding (LLM)
- Master-data matching (deterministic)
- Accounting validation (deterministic)
- API registration
- Manual-review handling

The LLM is used for document understanding only. It is not trusted to
perform financial calculations or make final accounting decisions.

## 2. Quick start

**Windows:**
```bash
# terminal 1
py accounting_api.py

# terminal 2
copy .env.example .env   # fill in GEMINI_API_KEY
py main.py --dry-run     # extraction + matching + validation, no POST
py main.py                # full run, registers to the accounting API
```

**macOS/Linux:**
```bash
# terminal 1
python3 accounting_api.py

# terminal 2
cp .env.example .env
python main.py --dry-run
python main.py
```

Developed and tested on Windows 10 (`py` launcher); commands above are
equivalent on macOS/Linux with `python3`.

## 3. Project structure

```
invoice-intake/
├── invoices/
│   ├── invoice_01.pdf
│   ├── invoice_04.jpg
│   └── ...
├── manual_review/
├── logs/
├── main.py
├── extractor.py
├── api_client.py
├── matcher.py
├── test_gemini.py
├── requirements.txt
├── .env
├── .gitignore
├── README.md
└── SUBMISSION.md
```

## 4. Technology stack

- Python 3.14, standard library
- `requests`
- `python-dotenv`
- `pydantic`
- `google-genai` (Google's current GenAI SDK, not the older
  `google-generativeai` package)

Accounting API: `http://localhost:8080`, auth via `X-API-Key: demo-key-1234`.

## 5. High-level architecture

```
invoices/ (PDF/JPG/PNG)
        |
        v
Gemini Multimodal Document Extraction
        |
        v
Structured Invoice JSON
        |
        +-------------------+
        |                   |
        v                   v
Partner Matcher       Business Rules
Name/Alias/Reg. No.   Tax/Amount/Date
        |                   |
        +---------+---------+
                  |
                  v
          Validation Passed
                  |
                  v
          POST /invoices
                  |
                  v
        Registered Invoice

Validation / Matching Failure
                  |
                  v
           manual_review/
```

## 6. API pre-check

Before invoice processing starts, the application loads master data once
per run (not once per invoice):

```
GET /partners
X-API-Key: demo-key-1234
```

```json
{
  "partner_code": "P-1001",
  "name": "株式会社サンプル",
  "registration_number": "T1010001000101",
  "aliases": ["サンプル株式会社", "株式会社サンプル商事"]
}
```

```
GET /tax-codes
X-API-Key: demo-key-1234
```

Expected tax codes: `T10` (10%), `T08` (8%). Extracted tax codes are
validated against this live master rather than hard-coded.

## 7. Invoice extraction

Supported formats: `.pdf`, `.jpg`, `.jpeg`, `.png`.

PDFs may contain a digital text layer or be a scanned image; both are
uploaded to Gemini as document input. Image files are sent as multimodal
image input.

## 8. Gemini model

Configurable via `.env`:

```
GEMINI_MODEL=gemini-3.7-flash
```

Kept out of business logic so it can be swapped without code changes.

## 9. Extraction prompt strategy

The extraction prompt instructs Gemini to:

- extract only information visible in the document
- avoid inventing missing values; return `null` for unreadable fields
- preserve Japanese supplier names as printed
- convert Japanese dates (including era dates) to Gregorian `YYYY-MM-DD`
- extract line-item amounts exactly as printed
- return monetary amounts as integers
- identify the tax registration number
- identify `T10`/`T08` tax codes
- inspect all pages of multi-page invoices
- **not** calculate or repair subtotal, tax, or total — that's done
  independently in Python

## 10. Structured output

```json
{
  "supplier_name": "株式会社サンプル",
  "tax_registration_number": "T1010001000101",
  "invoice_number": "INV-2026-001",
  "issue_date": "2026-08-01",
  "due_date": "2026-08-31",
  "line_items": [
    {
      "description": "商品A",
      "quantity": 2,
      "unit": "個",
      "unit_price": 50000,
      "amount": 100000,
      "tax_code": "T10"
    }
  ],
  "subtotal": 100000,
  "tax_amount": 10000,
  "total_amount": 110000
}
```

Gemini's structured JSON output mode is used to reduce parsing ambiguity;
the application additionally validates the returned structure against a
schema before doing anything else with it.

## 11. Supplier matching

Handled independently in `matcher.py`, in priority order:

1. Exact registration number
2. Exact supplier name
3. Exact alias
4. Normalized Japanese name comparison
5. Fuzzy matching

The registration number is treated as the strongest identifier, since it's
unique and unambiguous, unlike supplier names which can have close
variants.

**Why matching is separate from the LLM:** Gemini extracts
`supplier_name` and `tax_registration_number` only. The deterministic
application layer resolves `partner_code` from those values against the
live partner master — the model never selects or invents an accounting
partner code.

## 12. Business validation

Run before every `POST /invoices` call, mirroring the accounting API's own
checks so failures are caught locally first:

- **Subtotal:** `calculated_subtotal = sum(line_item.amount)`, compared
  against the extracted subtotal.
- **Tax:** computed per tax code, on that code's subtotal, floor-rounded —
  `tax = floor(taxable_subtotal × rate)` — then summed.
  Example: T10 subtotal ¥100,000 → tax ¥10,000; T08 subtotal ¥20,000 →
  tax ¥1,600; total tax ¥11,600.
- **Total:** `calculated_subtotal + calculated_tax`, compared against the
  extracted total.
- **Dates:** `due_date >= issue_date`, and both must be valid `YYYY-MM-DD`.

Any mismatch is treated the same way the accounting API itself would treat
it — as `AMOUNT_MISMATCH` (subtotal, tax, or total) or
`DUE_DATE_BEFORE_ISSUE_DATE` — and the invoice is routed to manual review
without being posted.

## 13. API registration

Only invoices that pass extraction validation, partner matching, and
business-rule validation are submitted:

```
POST /invoices
Content-Type: application/json
X-API-Key: demo-key-1234
```

Payload: partner code, supplier name, registration number, invoice number,
issue date, due date, line items, subtotal, tax amount, total amount.

## 14. API error handling

| Response | Meaning | Handling |
|---|---|---|
| `400 PARTNER_NOT_FOUND` | Supplier not in the partner master | Logged, sent to `manual_review/` |
| `400 UNKNOWN_TAX_CODE` | Tax code not recognized | Logged, sent to `manual_review/` |
| `400 DUE_DATE_BEFORE_ISSUE_DATE` | Date ordering invalid | Logged, sent to `manual_review/` |
| `409 DUPLICATE_INVOICE` | Already registered for this partner | Treated as an expected business signal, not retried |
| `422 AMOUNT_MISMATCH` | Subtotal/tax/total doesn't reconcile | Logged, sent to `manual_review/` |
| `422 VALIDATION_ERROR` | Malformed payload | Logged, sent to `manual_review/` |
| `401 UNAUTHORIZED` | Bad/missing API key | Treated as infrastructure failure — candidate for retry, not business review |

Locally-caught validation failures (before the API is even called) use
these same categories so `manual_review/` entries are consistent whether
the rejection came from local validation or from the API itself.

## 15. Manual review

Invoices that can't safely be processed automatically are written to
`manual_review/`, e.g. `manual_review/invoice-001.json`:

```json
{
  "source_file": "invoices/invoice-001.pdf",
  "reason": "AMOUNT_MISMATCH",
  "extracted": {
    "supplier_name": "株式会社サンプル",
    "invoice_number": "INV-001",
    "subtotal": 100000,
    "tax_amount": 9000,
    "total_amount": 109000
  }
}
```

## 16. Logging

Written to `logs/invoice_intake.log` and echoed to the console. Logged
events: master-data loading, invoice extraction, supplier match and match
method, validation failures, API responses, successful registration,
manual-review decisions.

## 17. Dry-run mode

```bash
py main.py --dry-run          # Windows
python main.py --dry-run      # macOS/Linux
```

Runs extraction → validation → partner matching, but does not call
`POST /invoices`. Useful for safely testing new invoices or a new prompt
before touching the accounting system.

## 18. Normal execution

```bash
py main.py          # Windows
python main.py      # macOS/Linux
```

Scans `invoices/` and processes every supported file.

## 19. Configuration (`.env`)

```
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.7-flash

ACCOUNTING_API_BASE_URL=http://localhost:8080
ACCOUNTING_API_KEY=demo-key-1234

INVOICE_DIR=invoices
```

The Gemini API key must never be hard-coded or committed to Git.

## 20. Gemini API smoke test

```bash
py test_gemini.py          # Windows
python test_gemini.py      # macOS/Linux
```

Expected output: `Gemini API is working.`

The SDK may emit a warning like *"Direct use of automatic function
calling (AFC) in Models.generate_content is not recommended..."* — this is
informational, not a failure. This pipeline doesn't use tool/function
calling, so it has no functional impact; it can be silenced by explicitly
disabling AFC in the call if desired.

## 21. Security considerations

**In this prototype:**
- API keys via environment variables, never hard-coded
- accounting API authentication on every call
- deterministic financial calculations (no LLM-computed numbers reach the ledger)
- partner master verification before registration
- manual review for anything unsafe to auto-post

**Production should additionally add:**
- a secrets manager
- HTTPS
- API-key rotation and access control
- encrypted invoice storage
- malware scanning on inbound documents
- PII/financial-document retention policy
- audit logging
- a data-processing review for sending financial documents to a
  third-party LLM API

## 22. Idempotency

The mock API rejects duplicates with `409 DUPLICATE_INVOICE`, keyed on
`partner_code + invoice_number`. That's a sufficient backstop for this
exercise, but a production system should maintain its own idempotency
record (file hash, invoice number, partner code, processing status,
timestamp, API response) so a duplicate is caught *before* an LLM call is
even made, not just before registration.

## 23. Retry strategy

**Retry with exponential backoff** (transient/infrastructure failures):
`429`, `500`, `502`, `503`, `504`, connection timeout — e.g. 1s, 2s, 4s,
8s, with a max retry count.

**Do not retry** (business-level failures — retrying won't change the
outcome): `AMOUNT_MISMATCH`, `DUPLICATE_INVOICE`, `PARTNER_NOT_FOUND`,
`UNKNOWN_TAX_CODE`, `DUE_DATE_BEFORE_ISSUE_DATE`, `VALIDATION_ERROR`.

(Not yet implemented in this prototype — documented here as the intended
production behavior; see `SUBMISSION.md` section 3 for why it was cut.)

## 24. Production risks

**OCR/vision accuracy.** Japanese invoices can have small fonts,
low-resolution scans, handwritten fields, stamps, and complex tables.
Failure modes include digit transposition (¥1,000,000 → ¥100,000), tax
rate confusion (8% → 10%), and date misreads. Mitigated by: structured
output, independent recalculation, registration-number-first matching,
manual review, and original-document retention for audit.

**Japanese era dates.** Invoices may use era notation (令和8年8月1日)
instead of the Gregorian form. The model is instructed to convert these;
production should consider a deterministic era-date parser as a backstop
rather than relying solely on the model.

**Tax rules.** This assignment specifies flat `T10`/`T08` floor-rounded
rates. Real Japanese consumption tax has more nuance (reduced rates,
taxable/non-taxable categories, rounding policy variation by company).
This implementation follows the assignment's rules rather than
implementing the full tax system.

**Supplier matching.** Fuzzy name matching alone is risky when suppliers
have similar names — registration number is preferred, and a production
system should require a high-confidence match or human approval when
multiple candidates are close.

**LLM hallucination.** Mitigated by an explicit extraction-only prompt,
structured output, `null` for unavailable fields, no financial
calculation by the model, local reconciliation, partner-master
verification, and manual review as the final gate.

**Duplicate processing.** If a file stays in the input directory, a later
run could reprocess it. The accounting API's own duplicate check is a
final safeguard; production should also maintain persistent processing
state so duplicates are caught before an LLM call is spent on them.

**API availability.** Production should distinguish transient
infrastructure failures from permanent business-validation failures and
retry only the former.

## 25. Observability (production target)

```
invoices_processed_total
invoices_success_total
invoices_manual_review_total
extraction_failure_total
validation_failure_total
partner_match_failure_total
duplicate_invoice_total
api_failure_total
average_processing_time
average_llm_latency
```

## 26. Cost estimate

Roughly $0.01–$0.03 per invoice, driven by Gemini model choice, input
document size/resolution, page count, and output token count. This is an
engineering estimate based on assignment-scale usage, not a benchmark —
representative invoices should be measured before any production
deployment. See `SUBMISSION.md` section 7 for the monthly projection.

## 27. Cost optimization (future work)

- Use a lower-cost multimodal model for straightforward invoices; escalate
  low-confidence extractions to a stronger model
- Avoid reprocessing already-registered files
- Resize unnecessarily large images before sending
- Skip irrelevant pages in multi-page documents
- Cache master data per run (already done)
- Keep the extraction schema compact; limit output to required fields

```
Cheap Vision Model
       ↓
Confidence Check
       ↓
Low confidence?
   /        \
 No          Yes
 |            |
Submit    Stronger Model
```

## 28. Why Python owns the accounting logic

The LLM is probabilistic; accounting calculations must be deterministic.

- **LLM:** "What does the invoice say?"
- **Python:** "Does the invoice make accounting sense?"
- **API:** "Should this validated invoice be registered?"

This separation is the core safety decision behind the design.

## 29. Design principles

- **Fail closed** — if something can't be verified, don't auto-register it.
- **Deterministic financial logic** — tax/subtotal/total computed locally.
- **Master-data driven** — partner and tax codes come from the live API,
  not hard-coded.
- **AI-assisted, not AI-controlled** — Gemini extracts; it doesn't decide.
- **Auditable** — every failure has a reason, and extracted data is
  preserved for manual review.
- **Configurable** — API URLs, keys, invoice directory, and model are all
  environment-driven.

## 30. Final assessment

The strongest production-safety decision in this design is that an
invoice must pass local reconciliation before it ever reaches the
accounting API — the LLM never gets to post a number it invented or
misread. This implementation is a working prototype suitable for the
assignment's 8-hour scope; production priorities are tracked in
`SUBMISSION.md` section 8.