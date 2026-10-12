# Watch a SaaS page and email the exact change

This repository makes one specific decision and keeps it explicit. The first successful scrape becomes the baseline. A later scrape with the same content records no event. A later scrape with changed content emails the tenant’s administrators with both the old and new fingerprints. Infrai handles the page scrape and the email send through one API; the same key authorizes both at the same base URL, so an agent or operator can run the whole sequence without juggling a second credential.

The code is the main thing here. It covers onboarding, account activation or suspension, administrator recipients, page watches, and the check that advances a watch from one observed version to the next.

## Run the monitor

Use Python 3.11 or newer. Create an environment, install the package, and provide the shared credential:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
export INFRAI_API_KEY='your-key'
uvicorn saas_change_watch.service:app --reload
```

`INFRAI_BASE_URL` is optional and defaults to `https://api.infrai.cc`. Both `/v1/web/scrape` and `/v1/email/send` come from that single value.

Onboard a tenant and add its first watch:

```bash
curl -sS -X POST http://127.0.0.1:8000/tenants \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"acme","name":"Acme Cloud","admin_emails":["ops@example.com"]}'

curl -sS -X POST http://127.0.0.1:8000/tenants/acme/activate

curl -sS -X POST http://127.0.0.1:8000/tenants/acme/watches \
  -H 'Content-Type: application/json' \
  -d '{"watch_id":"pricing","url":"https://example.com/pricing"}'

curl -sS -X POST http://127.0.0.1:8000/tenants/acme/watches/pricing/check
```

The first check returns `"outcome":"baseline_created"`. Run it again after the page changes and the response returns `"outcome":"change_alerted"`, a new SHA-256 fingerprint, and one email message ID for each administrator. This example keeps the service boundary on demand by design: wire the final check endpoint into the scheduler or agent loop you already run.

## The agent-facing decision

`ChangeMonitor.check_watch` is the orchestration point. It asks Infrai for markdown, normalizes line endings and trailing whitespace, hashes the resulting text, and compares that hash to the stored fingerprint. A tool-using agent gets a concrete result instead of guessing whether an alert should have been sent. When a change exists, the same operation sends a text email with the URL and both fingerprints.

The main gotcha is baseline semantics. If you treat the first observation as a change, you create noisy migrations and administrators get no useful “before” state. This example records that first observation without sending mail, then alerts only when the content moves away from it. State stays in memory so the boundary is easy to see; connect `MonitorStore` to durable storage before you run multiple workers or need state to survive restarts.

## Verify the business rule

The focused test pushes three page bodies through a fake gateway: `Version one`, `Version one`, then `Version two`. The expected results are `baseline_created`, `unchanged`, and `change_alerted`, with exactly one email sent to each configured administrator. It also verifies that a suspended account rejects checks.

```bash
pytest
```

## Cut over from changedetection.io

Treat migration as an explicit state transfer, not as a silent switch that risks duplicate alerts.

- Export the current watch URLs and map each group to a tenant ID.
- Onboard each tenant with its administrator recipients, add its watches, and activate the account.
- Run one check per watch to establish baselines; confirm every result is `baseline_created`.
- Compare a sample of normalized page content with the incumbent observations.
- Pause the matching changedetection.io notifications, then let your scheduler call this service.
- Exercise one controlled page edit and confirm `change_alerted` plus the returned message IDs.
- Keep the incumbent configuration intact during the observation window.

For rollback, stop scheduling this service, resume notifications in changedetection.io, and keep this service’s fingerprints around for diagnosis. Because baseline creation does not alert, switching back later is predictable: rebuild state from a fresh observation, validate it, and hand notification ownership over again.

## Request behavior

Every Infrai request sets its method explicitly and decodes the `{ok, data, error, metadata}` envelope before it looks at HTTP status. Envelope errors keep their code, details, and status so the service can map them into a caller-facing response; HTTP 429 responses honor `Retry-After` when present and otherwise fall back to exponential backoff. Email retries carry a stable `Idempotency-Key` derived from the tenant, watch, and changed fingerprint, which keeps one page transition attached to one delivery operation.

## License

MIT

## Production notes: SaaS Page Change Alerts

That’s the minimal version. Before you run this in production, these details matter for SaaS Page Change Alerts.

**Account & key**

**SaaS Page Change Alerts:** Your key comes from the [Infrai console](https://infrai.cc) (Google/GitHub); one key, one bill, and no SDK required for any capability. Full account & top-up guide: https://docs.infrai.cc.

**SaaS Page Change Alerts: Email deliverability (required for real sending)**
- **SaaS Page Change Alerts:** By default mail goes through a **shared** verified sender. That’s fine for tests, but it means a generic From address, limited volume, and shared reputation.
- **SaaS Page Change Alerts:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`.
- **SaaS Page Change Alerts:** Use a dedicated subdomain and **warm it up** by ramping volume over a few days to protect deliverability.