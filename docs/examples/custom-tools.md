# Example custom tools (Phase 24.b)

A curated gallery of importable [Phase 18](../roadmap.md) **custom-tool** definitions
ships with the repo. Each is wired to a **real, keyless public API**, so it works the
moment you import it — no signup, no API key. They appear in the **Examples** gallery
on the `/tools` page.

One click **Import** does two things:

1. creates the custom tool for the current profile (it immediately becomes callable
   from chat as `custom__<name>`), and
2. drops a ready-made **test payload** into the inline test panel so you can press
   **Invoke** and see a live response right away.

The definitions live in
[`backend/app/examples/custom_tool_examples.py`](../../backend/app/examples/custom_tool_examples.py)
and are served read-only from `GET /v1/tools/custom/examples`. CI smoke tests
(`backend/tests/test_phase24.py`) validate that every example is a valid tool
definition, imports through the real endpoint, and that its test payload and any URL
placeholders line up with the declared parameters. An opt-in live test
(`RUN_LIVE_EXAMPLE_TESTS=1`) actually calls each API.

## URL path templating

Custom tools now support `{placeholder}` tokens in the endpoint URL. On invocation
each `{name}` is replaced (URL-encoded) by the matching argument, and that argument is
**consumed** (not also sent as a query param / JSON body). This is what lets the
Wikipedia and Nager.Date examples drive path-based REST endpoints such as
`…/PublicHolidays/{year}/{countryCode}`. Implemented in
[`custom_tool_service._apply_path_params`](../../backend/app/services/custom_tool_service.py).

---

## 1. Currency conversion — `frankfurter.app`

**id:** `currency-convert` · `GET https://api.frankfurter.app/latest`

Converts an amount between currencies at the latest ECB reference rate.

- **Parameters:** `amount` (number), `from` (ISO code), `to` (ISO code) — sent as query params.
- **Test payload:** `{"amount": 10, "from": "USD", "to": "EUR"}`

## 2. Wikipedia summary — `en.wikipedia.org` (REST)

**id:** `wikipedia-summary` · `GET …/api/rest_v1/page/summary/{title}`

Returns the title, description and lead extract of an English Wikipedia article.

- **Parameters:** `title` (string) — substituted into the URL path via templating.
- **Header:** ships a descriptive `User-Agent` — Wikimedia's REST API returns HTTP 403 without one.
- **Test payload:** `{"title": "Python (programming language)"}`

## 3. Public holidays — `date.nager.at`

**id:** `public-holidays` · `GET …/api/v3/PublicHolidays/{year}/{countryCode}`

Lists a country's public holidays for a year.

- **Parameters:** `year` (integer), `countryCode` (ISO-2) — both substituted into the URL path.
- **Test payload:** `{"year": 2026, "countryCode": "IT"}`

## 4. Geocoding — `open-meteo.com`

**id:** `geocode` · `GET https://geocoding-api.open-meteo.com/v1/search`

Resolves a place name to coordinates, country and timezone.

- **Parameters:** `name` (string), `count` (integer) — sent as query params.
- **Test payload:** `{"name": "Milano", "count": 1}`

## 5. Bearer-auth template — `httpbin.org` (demo)

**id:** `bearer-auth-template` · `GET https://httpbin.org/bearer`

A **starting-point scaffold** — not a finished tool. It shows how to attach a bearer
token: the endpoint's `auth` is `{ "type": "bearer", "token": "replace-with-your-token" }`,
which sends `Authorization: Bearer <token>`. It points at httpbin's `/bearer` echo
endpoint so you can see auth working; swap the URL, token, and parameters for your own
API. The `header`-type auth (arbitrary `X-Api-Key: …` header) is configured the same way
in the tool form.

- **Test payload:** `{}`

---

## Adding a new example

1. Append an entry to `CUSTOM_TOOL_EXAMPLES` in
   [`backend/app/examples/custom_tool_examples.py`](../../backend/app/examples/custom_tool_examples.py):
   gallery metadata (`id`, `title`, `description`, `category`, `api`), the importable
   `tool` (a `CustomToolIn`), and a `test_arguments` payload.
2. Prefer keyless public APIs so the example works with no setup. Use `{placeholders}`
   for path parameters; declare every placeholder and every `test_arguments` key in the
   tool's `parameters`.
3. Add a section here.
4. Run the smoke tests: `pytest tests/test_phase24.py`; optionally
   `RUN_LIVE_EXAMPLE_TESTS=1 pytest tests/test_phase24.py -k live` to hit the real APIs.
