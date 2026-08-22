# Submission

- Name: Rejoanul Alam
- Submission date (YYYY-MM-DD): 2026-08-22
- Hours actually spent: 8
- Repository / how to run it: https://github.com/rejoan/invoice-intake.
  Start with `py accounting_api.py` (separate terminal — Windows; use
  `python3 accounting_api.py` on macOS/Linux), then `py main.py`
  (`python main.py` on macOS/Linux).

## 1. Understanding the request

The client's stated problem was narrow: accounting staff retype invoices by
hand every month, it costs overtime at month-end close, and a typo nearly
caused a duplicate payment.

The problem I actually set out to solve is broader than "read invoices with
AI": it's *safely* automating data entry without reproducing the exact
failure mode the CEO described. An OCR/LLM pipeline that extracts numbers
and posts them straight into the accounting system just replaces a human
typo with a model hallucination — same risk, different source. So the real
requirement is a pipeline that (a) extracts structured data from documents
that vary in format (clean PDF text, scanned PDF, scanned images, some
handwritten), (b) resolves the printed Japanese supplier name to the
correct partner code in the existing accounting master, (c) independently
verifies the extracted numbers add up before anything is registered, and
(d) never silently posts something it isn't confident about — it routes
that to a human instead. Trust in the numbers, not raw automation coverage,
is the actual deliverable.

## 2. What you would have asked the client

| What you wanted to ask | The assumption you made | Why |
|---|---|---|
| Should one bad invoice block the whole month's batch, or be skipped and reported? | Skip and report individually; the batch keeps running | A single malformed invoice shouldn't hold up the other 11 during a real month-end close |
| What counts as "confident enough" to auto-register vs. send to a human? | Any business-rule validation failure, unresolved/ambiguous partner match, or extraction the model itself flags as uncertain goes to manual review; everything else auto-registers | The CEO's own example was a near-duplicate payment — the design should be biased toward under-automating rather than over-automating |
| How do invoices actually arrive in production (email attachment, scanned by staff, physical mail)? | Assumed a local folder drop, matching how the sample invoices were provided | No ingestion channel was specified, and building a channel-specific integration wasn't part of the assignment |
| Is a fuzzy supplier-name match (no registration number, no exact name) ever acceptable to auto-post? | No — treated as manual review even if similarity score is high | Registration number is the one reliable identifier; a wrong supplier match means paying the wrong company |
| Who reviews items sent to manual review, and how fast? | Assumed same-day review by accounting staff before close; no reviewer UI was built in the 8-hour budget, but the output format (JSON per invoice) is designed to be easy to act on | Building a full review UI wasn't feasible in scope, but the review handoff still needed to be usable, not just a log entry |
| Is it acceptable to send invoice images/PDFs to a third-party LLM API, given this is a Japanese company's financial documents? | Assumed acceptable for this prototype/trial, since the assignment explicitly expects a third-party LLM key | Flagged as a real production concern in section 7 rather than silently ignored |

## 3. Scoping decisions

**What you built**

- End-to-end pipeline: document ingestion (PDF text-layer, scanned PDF,
  JPG/PNG) → Gemini multimodal extraction → schema-validated structured
  JSON → independent partner matching → independent recalculation of
  subtotal/tax/total → `POST /invoices` → structured error handling per API
  error code → manual-review JSON output for anything that doesn't clear
  every gate.
- Supplier matching with an explicit priority order (registration number →
  exact name → alias → normalized name → fuzzy), because financial
  correctness shouldn't depend on the LLM's guess of a partner code.
- A single-command startup and a `--dry-run` mode that runs extraction,
  matching, and validation without touching the accounting API.
- Structured logging of every stage for auditability.

**What you left out, and why**

- *Persistent processing database / idempotency store.* With 12 sample
  invoices and an in-memory mock API, the accounting API's own
  `DUPLICATE_INVOICE` check is sufficient for this exercise; a durable
  store is a real production need but not required to demonstrate the
  concept in 8 hours.
- *Human review UI.* Writing reviewable JSON to `manual_review/` proves the
  "don't auto-post what you're not sure of" gate exists without spending
  several hours on a frontend that wasn't the point of the exercise.
- *Confidence scoring beyond pass/fail.* Calibrated confidence thresholds
  are their own research problem; I used binary validation gates
  (schema valid / amounts reconcile / partner resolved) instead of a
  continuous confidence score.
- *Automated retry/backoff for transient failures.* I documented the
  strategy (exponential backoff for 429/5xx, no retry for business errors)
  but didn't implement a retry loop, since none of the 12 sample calls
  needed it and it wasn't worth the time against higher-value items.
- *Comprehensive automated test suite.* I validated behavior by running
  all 12 sample invoices through the pipeline rather than writing unit
  tests, given the time budget.
- *Deterministic Japanese-era date parser.* I rely on the model to convert
  era dates (e.g. 令和8年) to Gregorian and spot-checked the output rather
  than building a dedicated parser.

## 4. Design and technology choices

**Flow:** `invoices/` (PDF/JPG/PNG) → Gemini multimodal extraction →
structured invoice JSON → split into two independent checks (partner
matcher against `/partners`; business-rule validator that recalculates
subtotal/tax/total from the line items) → if both pass, `POST /invoices` →
if either fails, or the API rejects it, write to `manual_review/`.

**LLM/OCR choice:** Google Gemini via the `google-genai` SDK, chosen
because it accepts PDFs and images natively as multimodal input in one
call (no separate OCR step), handles Japanese text well, and has a usable
free tier — relevant since the assignment requires bringing your own key.

**What I decided against:**
- *A separate OCR engine (Tesseract / a dedicated document-AI service) feeding
  a text-only LLM.* A two-stage OCR-then-LLM pipeline adds a failure
  surface (OCR errors compound into extraction errors) for no benefit here,
  since a modern multimodal model reads the image directly. I do describe a
  two-stage *cheap-model-then-expensive-model* architecture in section 7 as
  a future cost optimization — that's a different axis (cost tiering, not
  OCR-vs-multimodal).
- *Letting the LLM choose the accounting `partner_code` directly.* The
  model extracts what's printed (name, registration number); a
  deterministic Python layer resolves that to a partner code. An LLM
  should not be the thing deciding which ledger account gets debited.
- *Letting the LLM perform or "repair" the arithmetic.* The prompt
  explicitly forbids the model from calculating or fixing
  subtotal/tax/total — Python recomputes everything independently, since
  accounting math must be exact and auditable, not probabilistic.
- *Building retry/queue infrastructure up front.* Correctness of the
  validation gate was a better use of 8 hours than resilience
  infrastructure for a 12-invoice batch.

## 5. How you used AI, and how you checked it

**What you delegated to AI**

Document field extraction only: supplier name, tax registration number,
invoice number, issue/due dates, line items (description, quantity, unit,
unit price, amount, tax code), subtotal, tax amount, total amount. The
prompt explicitly instructs the model to extract only what's visible,
return `null` for unreadable fields, never invent values, and never
calculate or repair totals.

**How you verified the output**

- Independent recomputation: subtotal = sum of line amounts, tax computed
  per tax code on that code's subtotal with floor rounding, total =
  subtotal + tax — computed in Python and compared against what Gemini
  returned. Any mismatch blocks registration and routes to manual review.
- Partner resolution is never taken from the model's raw text match; it's
  resolved against the live `/partners` master by registration number
  first, exact/alias name second.
- Structural validation (types, required fields, date format) before
  anything reaches the accounting API, so a malformed extraction fails
  fast and visibly instead of being silently coerced.

**A case where the AI got it wrong**

`invoice_12.jpg`: Gemini extracted a negative value for line item 3's
`amount`. That's not a value that should ever reach the accounting API —
line amounts on a supplier invoice are non-negative — so local schema
validation rejected it (`Line 3: amount cannot be negative`) before any
API call was attempted, and the file was written to
`manual_review/invoice_12.json`. I didn't try to "fix" the sign in code,
since guessing whether it was a misread minus sign, a stray character on
the scan, or something else on the source document is exactly the kind of
silent correction this design is meant to avoid — a human needs to look at
the original image.

A second, more ambiguous case: `invoice_09.pdf` (the scanned-image-only
PDF) failed on `TOTAL_MISMATCH` — the extracted total was ¥147,497, my
locally recalculated total from the line items and tax was ¥147,496, a
one-yen difference. I can't tell from the log alone whether that's a
Gemini misread or a genuine rounding artifact on the source document
itself (Japanese consumption-tax rounding can differ by document). Either
way, the design doesn't try to guess which — a one-yen mismatch still
fails the reconciliation check and goes to manual review rather than being
silently accepted as "close enough."

## 6. Integrating with the accounting system

Actual results from a full run against all 12 sample invoices
(`logs/invoice_intake.log`): 8 registered, 4 sent to manual review.

| Invoice | Result | How you handled it |
|---|---|---|
| invoice_01.pdf | Registered — `ACC-0001` | Exact-name match to `P-1001`; validation passed; posted |
| invoice_02.pdf | Registered — `ACC-0002` | Exact-name match to `P-1004`; 26-line invoice, largest in the set (¥1,560,988 total); extraction and validation both passed |
| invoice_03.pdf | Registered — `ACC-0003` | Exact-name match to `P-1003`; posted |
| invoice_04.jpg | Registered — `ACC-0004` | Exact-name match to `P-1002`; posted |
| invoice_05.jpg | Registered — `ACC-0005` | Exact-name match to `P-1005`; posted |
| invoice_06.jpg | Registered — `ACC-0006` | Exact-name match to `P-1001`; posted |
| invoice_07.jpg | `409 DUPLICATE_INVOICE` → manual review | Matched partner `P-1001`, but the extracted invoice number (`YM-2026-0107`) collided with the number already registered from `invoice_01.pdf`. Not blindly retried — written to `manual_review/invoice_07.json` for a human to confirm whether it's a true duplicate or a misread invoice number |
| invoice_08.jpg | Registered — `ACC-0007` | Exact-name match to `P-1003`; posted |
| invoice_09.pdf | `TOTAL_MISMATCH` (off by ¥1) → manual review | Locally recalculated total (¥147,496) didn't match the extracted total (¥147,497). One-yen mismatches are rejected the same as any other — no tolerance band — and written to `manual_review/invoice_09.json` |
| invoice_10.jpg | Partner match below threshold (score 0.364) → manual review | No exact, alias, or registration-number match found; fuzzy-match confidence too low to auto-post; written to `manual_review/invoice_10.json` rather than guessing the supplier |
| invoice_11.jpg | Registered — `ACC-0008` | Exact-name match to `P-1002`; posted |
| invoice_12.jpg | Schema validation failure (negative line amount) → manual review | Gemini returned a negative `amount` for line 3; rejected by local schema validation before any API call; written to `manual_review/invoice_12.json` |

**Summary:** 8/12 registered automatically, 4/12 correctly stopped short of
registration — one duplicate, one amount mismatch, one low-confidence
partner match, one invalid extracted value. None of the 4 failures were
silently forced through; each has a specific, logged reason and a
preserved source reference for the reviewer.

General handling, independent of any single invoice: `PARTNER_NOT_FOUND`,
`UNKNOWN_TAX_CODE`, `DUE_DATE_BEFORE_ISSUE_DATE`, `AMOUNT_MISMATCH`, and
`VALIDATION_ERROR` are all treated as business-level failures — logged and
written to `manual_review/`, never retried automatically.
`DUPLICATE_INVOICE` (409) is treated as an expected business signal, not an
error to recover from — it means the invoice is already registered, so
processing for that file stops without re-posting. `UNAUTHORIZED` and
connection failures are treated as infrastructure failures, distinct from
the above, and are the class of error that should be retried with backoff
in production.

## 7. Cost, limits, and risk in production

- **Cost per invoice:** $0 in this test run — I used the Gemini free tier
  for development, so I don't have real billed-cost numbers. Based on
  `gemini-2.5-flash` published pricing and the size of these documents
  (single-page invoices, one 26-line multi-page exception), a paid-tier
  estimate is roughly $0.01–$0.03 per invoice, driven mainly by
  input size (image/PDF resolution, page count) and output token count for
  the structured JSON. This is an engineering estimate, not a measured
  benchmark, and should be confirmed against actual billing before relying
  on it.
- **Monthly cost at 1,000 invoices per month:** Roughly $10–$30/month at
  the above per-invoice estimate; compute/hosting cost is negligible at
  this volume. Note this assumes moving off the free tier — the free
  tier's request-per-minute/per-day quotas would likely be the first thing
  hit at 1,000/month long before cost becomes the binding constraint.
- **Processing time per invoice:** Measured from `logs/invoice_intake.log`
  across all 12 invoices: 14–89 seconds each, averaging ~34 seconds
  (12 invoices took 6 min 43 sec end to end on the free tier). The range
  is wide — simple single-page invoices finished in 14–26s, while the
  largest invoice (26 line items, `invoice_02.pdf`) and a couple of image
  invoices took 40–89s. Free-tier rate limiting is the most likely cause
  of the slower calls, so paid-tier latency would likely be both faster
  and more consistent.
- **Where this breaks first:** The pipeline is a single synchronous
  process with no concurrency or queue. At ~34s/invoice average, 1,000
  invoices/month is only ~9.5 hours of total processing time, so raw
  throughput isn't the first wall — the free tier's rate limits are: the
  test run above already showed call times varying 4x (14s to 89s) across
  otherwise similar single-page invoices, consistent with throttling.
  Moving to a paid tier and adding concurrency would remove that ceiling,
  but the in-memory-style duplicate check and the flat-file
  `manual_review/` output still wouldn't scale past a small batch — they'd
  need a real database and a reviewer UI respectively before this could
  run unattended at production volume.
- **How you would find out if something was registered incorrectly:**
  The accounting API already rejects internally inconsistent amounts
  (it recalculates from line items), so most "obviously wrong" numbers
  never get posted. The real risk is a value that's *wrong but internally
  consistent* — e.g. a correctly-matched partner with a subtly misread
  line amount that still balances. Production should run periodic
  reconciliation (e.g. monthly total registered vs. supplier statement
  totals), keep the source document linked to every registered record for
  spot-checking, and log full extraction output alongside the API
  response so any discrepancy can be traced back to what the model saw.

## 8. What you would do with another 8 hours

1. **A human review UI for `manual_review/`.** This is the highest-leverage
   next step because it directly targets the CEO's stated pain (overtime,
   error-prone manual entry) — right now, review means opening JSON files,
   which doesn't actually save accounting staff time. A simple approve/
   correct/reject screen would.
2. **A persistent processing/idempotency store.** The in-memory duplicate
   check in the mock API is fine for a demo but isn't a production
   safety net; a real store (file hash, invoice number, partner code,
   status, timestamp) is the next thing that matters once this runs
   unattended every month.
3. **Confidence-based two-tier extraction (cheap model → escalate on low
   confidence).** This addresses both cost and reliability at scale, but
   it's ordered last because it's an optimization on top of a pipeline
   that already works — items 1 and 2 are correctness/usability gaps,
   this is efficiency.