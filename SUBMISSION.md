Automated Invoice Intake Pipeline

1. Overview

This project implements an automated Japanese invoice intake pipeline that processes PDF and image-based invoices, extracts structured invoice information using Google's Gemini multimodal AI, validates accounting rules locally, matches the supplier against a partner master, and registers the invoice with a local mock accounting system API.

The pipeline deliberately separates:

Document understanding

Master-data matching

Accounting validation

API registration

Manual-review handling

The LLM is used for document understanding only. It is not trusted to perform financial calculations or make final accounting decisions.

2. Project Structure

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
└── SUBMISSION.md

3. Technology Stack

Python 3.14

Standard Python libraries

requests

python-dotenv

pydantic

Google GenAI Python SDK (google-genai)

The implementation uses Google's current google-genai SDK rather than the older google-generativeai package.

The local accounting API is:

http://localhost:8080

Authentication:

X-API-Key: demo-key-1234

4. High-Level Architecture

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

5. API Pre-check

Before invoice processing starts, the application loads master data.

Partner Master

GET /partners
X-API-Key: demo-key-1234

The partner master contains information such as:

partner code

supplier name

registration number

aliases

Example:

{
  "partner_code": "P-1001",
  "name": "株式会社サンプル",
  "registration_number": "T1010001000101",
  "aliases": [
    "サンプル株式会社",
    "株式会社サンプル商事"
  ]
}

The partner master is loaded once per execution rather than once per invoice.

Tax Code Master

GET /tax-codes
X-API-Key: demo-key-1234

Expected supported tax codes:

T10 = 10%
T08 = 8%

The application validates extracted tax codes against the current accounting-system master.

6. Invoice Extraction

Supported formats:

.pdf
.jpg
.jpeg
.png

PDF invoices may contain digitally generated text or scanned pages. Image invoices are processed as multimodal image input.

PDF documents are uploaded to Gemini and processed as document input.

7. Gemini Model

The model is configurable through .env:

GEMINI_MODEL=gemini-3.7-flash

The model is not hard-coded into business logic so it can be changed without modifying Python code.

8. Gemini Prompt Strategy

The extraction prompt instructs Gemini to:

extract only information visible in the document

avoid inventing missing values

return null for unreadable fields

preserve Japanese supplier names

convert Japanese dates to Gregorian YYYY-MM-DD

extract line-item amounts exactly

return monetary amounts as integers

identify invoice registration numbers

identify T10/T08 tax codes

inspect all pages of multi-page invoices

The model is explicitly instructed:

Do NOT calculate or repair subtotal, tax, or total.

Financial calculations are performed independently by Python.

9. Structured Output

Expected structure:

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

Gemini structured JSON output is used to reduce parsing ambiguity. The application additionally validates the returned structure.

10. Supplier Matching

Supplier matching is handled independently in matcher.py.

Matching priority:

Exact registration number

Exact supplier name

Exact alias

Normalized Japanese name comparison

Fuzzy matching

The registration number is treated as the strongest identifier.

Example:

Extracted:
株式会社サンプル

Registration:
T1010001000101

Partner:
P-1001

The application resolves the partner code rather than asking the LLM to invent one.

11. Why Matching Is Separate From the LLM

Gemini extracts:

supplier_name
tax_registration_number

The deterministic application layer determines:

partner_code

This prevents an LLM from incorrectly selecting or inventing an accounting partner.

12. Business Validation

Validation occurs before POST /invoices.

Subtotal

calculated_subtotal = sum(line_item.amount)

Then:

calculated_subtotal == invoice.subtotal

If not, the invoice is rejected with:

SUBTOTAL_MISMATCH

Tax

Tax is calculated independently:

T10 = 10%
T08 = 8%

For each tax code:

tax = floor(taxable_subtotal × rate)

Example:

T10 subtotal = 100,000
T08 subtotal = 20,000

T10 tax = floor(100,000 × 0.10) = 10,000
T08 tax = floor(20,000 × 0.08) = 1,600

Total tax = 11,600

Total

calculated_total = calculated_subtotal + calculated_tax

Then:

calculated_total == invoice.total_amount

Otherwise:

TOTAL_MISMATCH

Dates

The application verifies:

due_date >= issue_date

and validates YYYY-MM-DD.

13. API Registration

Only invoices that pass extraction, matching, and accounting validation are submitted.

POST /invoices
Content-Type: application/json
X-API-Key: demo-key-1234

Payload includes:

partner code

supplier name

registration number

invoice number

issue date

due date

line items

subtotal

tax amount

total amount

14. API Error Handling

Important expected errors include:

HTTP 400

Examples such as:

AMOUNT_MISMATCH

are logged and sent to manual review.

HTTP 409

DUPLICATE_INVOICE

is treated as a business-level duplicate and is not blindly retried.

HTTP 422

PARTNER_NOT_FOUND

is sent to manual review.

15. Manual Review

Invoices that cannot safely be processed automatically are written to:

manual_review/

Example:

manual_review/invoice-001.json

Example:

{
  "source_file": "invoices/invoice-001.pdf",
  "reason": "VALIDATION_FAILED",
  "extracted": {
    "supplier_name": "株式会社サンプル",
    "invoice_number": "INV-001",
    "subtotal": 100000,
    "tax_amount": 9000,
    "total_amount": 109000
  }
}

16. Logging

Logs are written to:

logs/invoice_intake.log

The console also receives processing information.

Important events include:

master-data loading

invoice extraction

supplier match

match confidence

validation failures

API responses

successful registration

manual-review decisions

17. Dry-Run Mode

Use:

python main.py --dry-run

This performs:

Extraction
    ↓
Validation
    ↓
Partner Matching

but does not call:

POST /invoices

This is useful for safely testing invoices.

18. Normal Execution

After configuration:

python main.py

The application scans:

invoices/

and processes all supported invoice files.

19. Gemini Configuration

.env:

GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.7-flash

ACCOUNTING_API_BASE_URL=http://localhost:8080
ACCOUNTING_API_KEY=demo-key-1234

INVOICE_DIR=invoices

The Gemini API key must never be hard-coded into Python source code or committed to Git.

20. Gemini API Test

Run:

python test_gemini.py

Successful output:

Gemini API is working.

The Google GenAI SDK may emit a warning similar to:

Direct use of automatic function calling (AFC) in Models.generate_content is not recommended...

This is a warning, not an API failure. It means the SDK is advising that automatic function calling is normally used through a chat interface. This invoice pipeline does not require function/tool calling, so the warning has no functional impact on the extraction architecture.

If desired, AFC can be explicitly disabled in the test call.

21. Security Considerations

The prototype uses:

environment variables for API keys

no hard-coded Gemini credentials

accounting API authentication

deterministic financial calculations

partner master verification

manual review for unsafe cases

Production should additionally use:

a secrets manager

HTTPS

API-key rotation

access control

encrypted invoice storage

malware scanning

PII retention policies

audit logging

22. Idempotency

Duplicate invoices are a production concern.

The mock API is expected to reject duplicates with:

DUPLICATE_INVOICE

A production implementation should also maintain an idempotency key based on a stable combination such as:

partner_code + invoice_number

or use a server-generated idempotency identifier.

A persistent processing database should record:

file hash

invoice number

partner code

processing status

timestamp

API response

23. Retry Strategy

Transient infrastructure failures such as:

HTTP 429
HTTP 500
HTTP 502
HTTP 503
HTTP 504
connection timeout

should be candidates for exponential backoff.

Example:

1 second
2 seconds
4 seconds
8 seconds

with a maximum retry count.

Business errors such as:

AMOUNT_MISMATCH
DUPLICATE_INVOICE
PARTNER_NOT_FOUND

should not be blindly retried.

24. Production Risks

OCR / Vision Accuracy

Japanese invoices may contain:

small fonts

low-resolution scans

handwritten fields

stamps

complex tables

Potential errors include:

1,000,000 → 100,000
8% → 10%
2026/08/01 → 2026/06/01

Mitigation:

structured output

deterministic validation

registration-number matching

manual review

confidence thresholds

original-document retention

Japanese Date Conversion

Japanese invoices can use:

令和8年8月1日

instead of:

2026-08-01

Gemini is instructed to convert Japanese era dates, but production should consider adding deterministic Japanese-era date parsing or a specialized document parser.

Tax Rules

This assignment specifies:

T10 = 10%
T08 = 8%

with floor rounding.

Real Japanese consumption-tax processing may be more complicated depending on invoice structure, tax category, rounding policy, taxable/non-taxable items, reduced tax rate, and accounting policy.

The implementation follows the assignment rules rather than attempting to implement the entire Japanese tax system.

Supplier Matching

Fuzzy matching can be dangerous when suppliers have similar names.

The registration number is therefore preferred over fuzzy supplier-name similarity.

A production system should require a high-confidence match or human approval when multiple candidates are similar.

LLM Hallucination

Gemini could theoretically produce information not actually visible in the invoice.

Mitigation:

Explicit extraction prompt

Structured output

null for unavailable fields

No financial calculations by the model

Local reconciliation

Partner master verification

Manual review

Duplicate Processing

If the same file remains in the input directory, a later run could attempt to process it again.

The accounting API's duplicate detection is a final safeguard. Production should also maintain persistent processing state.

API Availability

The local accounting API may become unavailable.

Production should distinguish transient infrastructure failures from permanent business validation failures and retry transient failures automatically.

25. Observability

A production version should expose metrics such as:

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

26. Cost Estimate

The assignment assumes approximately:

$0.01–$0.03 per invoice

Actual cost depends on:

Gemini model

input token/document size

number of pages

image resolution

output token count

current API pricing

The range is therefore an engineering estimate, not a guaranteed price.

Representative Japanese invoices should be benchmarked before production deployment.

27. Cost Optimization

Potential optimizations:

Use a lower-cost multimodal model for simple invoices.

Escalate low-confidence invoices to a stronger model.

Avoid repeated processing.

Resize unnecessarily large images.

Avoid sending irrelevant pages.

Cache master data during a processing run.

Keep the extraction schema compact.

Limit output fields to required accounting information.

A future two-stage architecture could be:

Cheap Vision Model
       ↓
Confidence Check
       ↓
Low confidence?
   /        \
 No          Yes
 |            |
Submit    Stronger Model

28. Why Python Owns the Accounting Logic

The LLM is probabilistic while accounting calculations must be deterministic.

Therefore:

LLM:
"What does the invoice say?"

Python:
"Does the invoice make accounting sense?"

API:
"Should this validated invoice be registered?"

This separation is a core safety decision.

29. 8-Hour Scope Trade-offs

Included

Gemini multimodal integration

PDF/image support

structured extraction

schema validation

partner matching

registration-number matching

fuzzy matching

tax validation

subtotal validation

total validation

date validation

accounting API integration

API error handling

manual review output

logging

CLI

dry-run mode

environment configuration

Deferred

persistent processing database

distributed job queue

advanced confidence scoring

human-review web interface

document image preprocessing

Japanese OCR fallback engine

sophisticated Japanese-era date parser

idempotency database

automated retry queue

monitoring dashboard

distributed tracing

secrets manager integration

encrypted document storage

comprehensive automated test suite

multi-tenant support

These are intentionally outside the core 8-hour assignment scope.

30. Testing Strategy

Case 1: Valid invoice

Extraction → PASS
Matching → PASS
Validation → PASS
POST → SUCCESS

Case 2: Subtotal mismatch

SUBTOTAL_MISMATCH
→ manual review

Case 3: Tax mismatch

TAX_MISMATCH
→ manual review

Case 4: Total mismatch

TOTAL_MISMATCH
→ manual review

Case 5: Invalid due date

DATE_INVALID
→ manual review

Case 6: Unknown supplier

PARTNER_NOT_FOUND_OR_AMBIGUOUS
→ manual review

Case 7: Duplicate invoice

DUPLICATE_INVOICE
→ no duplicate registration

Case 8: API unavailable

API connection failure
→ error logged
→ invoice not silently lost

31. Recommended Test Sequence

First verify Gemini:

py test_gemini.py

Then verify the accounting API:

curl -H "X-API-Key: demo-key-1234" http://localhost:8080/partners

Then perform a complete dry run:

py main.py --dry-run

Finally enable actual registration:

py main.py

32. Example Successful Workflow

invoice_01.pdf
       ↓
Gemini extraction
       ↓
Supplier: 株式会社サンプル
       ↓
Registration: T1010001000101
       ↓
Partner: P-1001
       ↓
Subtotal: 100,000
       ↓
T10: 10,000
       ↓
Total: 110,000
       ↓
Validation PASS
       ↓
POST /invoices
       ↓
201 Created

33. Example Failed Workflow

invoice_02.jpg
       ↓
Gemini extraction
       ↓
Subtotal: 100,000
       ↓
Line item sum: 105,000
       ↓
SUBTOTAL_MISMATCH
       ↓
NO API POST
       ↓
manual_review/invoice-002.json

34. Design Principles

Fail closed

If important information cannot be verified, do not register the invoice automatically.

Deterministic financial logic

Tax, subtotal, and total calculations are performed locally.

Master-data driven

Partner codes and tax codes come from the accounting API.

AI-assisted, not AI-controlled

Gemini extracts information but does not make final accounting decisions.

Auditable

Every failure has a reason and extracted data can be preserved for manual review.

Configurable

API URLs, API keys, invoice directory, and Gemini model are configured through environment variables.

35. Final Assessment

The implementation provides a practical foundation for automated Japanese invoice intake while maintaining a clear boundary between probabilistic document extraction and deterministic accounting logic.

The strongest production-safety decision is that an invoice must pass local reconciliation before it reaches the accounting API.

The current implementation is suitable as an approximately 8-hour engineering assignment and prototype.

Recommended production priorities:

Persistent idempotency and processing state

Better Japanese document preprocessing

Confidence-based human review

Automated transient API retries

Comprehensive automated tests

Monitoring and alerting

Security/privacy review for external AI processing

Production-grade Japanese tax-rule validation