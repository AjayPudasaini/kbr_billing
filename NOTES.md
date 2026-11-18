# My Notes

## 1. Hardcoded Secrets in `config/settings.py`
- **Issue:** Sensitive configurations (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, payment gateway credentials) had hardcoded fallback and live secrets.
- **Impact:** Security risk and credential exposure in source code.
- **Fix:** Moved all secrets and configurations to `.env` and `.env.example` using `os.environ.get()`.

## 2. SQL Injection in `OutstandingCustomersView`
- **Issue:** The search query (`?q=`) was directly interpolated into raw SQL using an f-string (`LOWER('%%{search}%%')`).
- **Impact:** Allowed arbitrary SQL injection via user input.
- **Fix:** Switched to parameterized SQL execution with `LOWER(%s)` and passed query parameters securely.

## 3. Multi-Tenant IDOR in `InvoiceViewSet.payments`
- **Issue:** Used `Invoice.objects.get(pk=pk)` without business scoping.
- **Impact:** Allowed users from one business to record payments against another business's invoices.
- **Fix:** Replaced with `self.get_object()` so queries are strictly filtered by the authenticated user's business via `get_queryset()`.

## 4. Floating-Point Precision in Financial Calculations
- **Issue:** Currency amounts and VAT were calculated using native Python `float`.
- **Impact:** Inherent binary floating-point rounding errors in invoices and tax ledgers.
- **Fix:** Converted monetary calculations to `Decimal` with explicit `ROUND_HALF_UP` quantization to two decimal places.

## 5. Race Condition in Inventory Management
- **Issue:** Product stock was queried and decremented without row-level locking.
- **Impact:** Concurrent checkouts could cause race conditions and overselling (negative stock).
- **Fix:** Added `select_for_update()` to lock product records during atomic invoice creation.

## 6. Environment Loading via `python-dotenv`
- **Issue:** Without Docker, running `runserver` directly did not load the `.env` file into `os.environ`, raising `ImproperlyConfigured: The SECRET_KEY setting must not be empty.`
- **Impact:** Local development commands failed outside containerized environments.
- **Fix:** Added `python-dotenv` to `requirements.txt` and invoked `load_dotenv(BASE_DIR / ".env")` at the top of `config/settings.py`.
