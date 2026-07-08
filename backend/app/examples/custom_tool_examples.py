"""
Phase 24.b — curated, importable custom-tool definitions.

Each example is a Phase 18 custom tool wired to a **real, keyless public API** so
it works the moment it is imported: one click on the ``/tools`` page creates the
tool and drops a ready-made ``test_arguments`` payload into the inline test panel.

The definitions here are plain data shaped like ``CustomToolIn`` plus gallery
metadata and a sample payload. Two of them (Wikipedia, Nager.Date) rely on the
``{name}`` URL path templating added in ``custom_tool_service.invoke``.

Keep ``id`` values stable — referenced by docs/examples/custom-tools.md. A CI smoke
test (``tests/test_phase24.py``) validates every example imports and that the
``test_arguments`` and any ``{placeholders}`` line up with the declared parameters.
"""

from __future__ import annotations

# Data only — no behaviour. ``tool`` mirrors CustomToolIn; ``test_arguments`` is
# the pre-filled inline-test payload; ``api`` is a human label for the upstream.
CUSTOM_TOOL_EXAMPLES: list[dict] = [
    {
        "id": "currency-convert",
        "title": "Currency conversion",
        "description": (
            "Convert an amount between currencies at the latest reference rate "
            "(European Central Bank data, via frankfurter.app). No API key."
        ),
        "category": "finance",
        "api": "frankfurter.app",
        "test_arguments": {"amount": 10, "from": "USD", "to": "EUR"},
        "tool": {
            "name": "currency_convert",
            "description": (
                "Converts an amount from one currency to another using the latest "
                "exchange rate. Currencies are ISO 4217 codes (USD, EUR, GBP, JPY…)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount to convert"},
                    "from": {"type": "string", "description": "Source currency code, e.g. 'USD'"},
                    "to": {"type": "string", "description": "Target currency code, e.g. 'EUR'"},
                },
                "required": ["amount", "from", "to"],
            },
            "endpoint": {
                "url": "https://api.frankfurter.app/latest",
                "method": "GET",
                "timeout": 15.0,
            },
            "enabled": True,
        },
    },
    {
        "id": "wikipedia-summary",
        "title": "Wikipedia summary",
        "description": (
            "Fetch the lead summary of an English Wikipedia article via the "
            "Wikimedia REST API. Uses URL path templating ({title})."
        ),
        "category": "reference",
        "api": "en.wikipedia.org (REST)",
        "test_arguments": {"title": "Python (programming language)"},
        "tool": {
            "name": "wikipedia_summary",
            "description": (
                "Returns the title, description and extract (lead paragraph) of an "
                "English Wikipedia article. Pass the article title exactly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Article title, e.g. 'Alan Turing'"},
                },
                "required": ["title"],
            },
            "endpoint": {
                "url": "https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                "method": "GET",
                # Wikimedia's REST API rejects requests without a descriptive
                # User-Agent (HTTP 403), so ship one — see https://w.wiki/4wJS.
                "headers": {
                    "User-Agent": "SpiceSibyl/1.0 (https://github.com/lordraw77/spice-sibyl)",
                },
                "timeout": 15.0,
            },
            "enabled": True,
        },
    },
    {
        "id": "public-holidays",
        "title": "Public holidays",
        "description": (
            "List the public holidays of a country for a given year (Nager.Date). "
            "Path-templated by {year} and {countryCode}. No API key."
        ),
        "category": "reference",
        "api": "date.nager.at",
        "test_arguments": {"year": 2026, "countryCode": "IT"},
        "tool": {
            "name": "public_holidays",
            "description": (
                "Returns the public holidays for a country and year. countryCode is "
                "an ISO 3166-1 alpha-2 code (IT, US, DE, GB…)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "Four-digit year, e.g. 2026"},
                    "countryCode": {"type": "string", "description": "ISO-2 country code, e.g. 'IT'"},
                },
                "required": ["year", "countryCode"],
            },
            "endpoint": {
                "url": "https://date.nager.at/api/v3/PublicHolidays/{year}/{countryCode}",
                "method": "GET",
                "timeout": 15.0,
            },
            "enabled": True,
        },
    },
    {
        "id": "geocode",
        "title": "Geocoding",
        "description": (
            "Resolve a place name to coordinates, country and timezone using the "
            "Open-Meteo geocoding API. No API key."
        ),
        "category": "geo",
        "api": "open-meteo.com",
        "test_arguments": {"name": "Milano", "count": 1},
        "tool": {
            "name": "geocode",
            "description": (
                "Looks up a place by name and returns matching locations with "
                "latitude, longitude, country and timezone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Place name, e.g. 'Milano'"},
                    "count": {"type": "integer", "description": "Max results (default 1)"},
                },
                "required": ["name"],
            },
            "endpoint": {
                "url": "https://geocoding-api.open-meteo.com/v1/search",
                "method": "GET",
                "timeout": 15.0,
            },
            "enabled": True,
        },
    },
    {
        "id": "bearer-auth-template",
        "title": "Bearer-auth template",
        "description": (
            "A starting-point template that shows how to attach a bearer token to "
            "an authenticated API. It hits httpbin.org/bearer (which echoes the "
            "token) so you can see auth working — replace the URL and token with "
            "your own API."
        ),
        "category": "template",
        "api": "httpbin.org (demo)",
        "test_arguments": {},
        "tool": {
            "name": "authed_api_example",
            "description": (
                "Template for a bearer-authenticated API. Edit the endpoint URL, the "
                "token, and the parameters to match your service."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
            "endpoint": {
                "url": "https://httpbin.org/bearer",
                "method": "GET",
                "timeout": 15.0,
                "auth": {"type": "bearer", "token": "replace-with-your-token"},
            },
            "enabled": True,
        },
    },
]


def list_custom_tool_examples() -> list[dict]:
    """Return the curated custom-tool examples (stable order)."""
    return CUSTOM_TOOL_EXAMPLES
