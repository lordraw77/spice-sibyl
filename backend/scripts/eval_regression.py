#!/usr/bin/env python3
"""
Lightweight regression harness over the exported feedback dataset (Phase 19).

Re-runs every 👍-rated prompt against a model via the gateway and reports how
the new responses compare (word-overlap similarity vs the saved good answer).
Not a rigorous eval — a smoke check that a model/prompt change didn't regress
answers users already approved.

Usage:
  python eval_regression.py dataset.json --base-url http://localhost:8000/api/v1 \
      --email admin@example.com --password secret [--model groq/llama-3.1-8b-instant]
"""

import argparse
import json
import sys

import httpx


def similarity(a: str, b: str) -> float:
    """Crude word-overlap (Jaccard) similarity in [0, 1]."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="feedback-dataset.json from GET /v1/feedback/export")
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--model", default=None, help="override model (default: each row's own)")
    parser.add_argument("--threshold", type=float, default=0.2,
                        help="similarity below this flags a regression (default 0.2)")
    args = parser.parse_args()

    with open(args.dataset, encoding="utf-8") as fh:
        rows = json.load(fh)
    rows = [r for r in rows if r.get("rating") == 1 and r.get("prompt")]
    if not rows:
        print("No 👍-rated rows with a prompt in the dataset — nothing to check.")
        return 0

    with httpx.Client(base_url=args.base_url, timeout=120.0) as client:
        auth = client.post("/auth/login", json={"email": args.email, "password": args.password})
        auth.raise_for_status()
        token = auth.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        failures = 0
        for i, row in enumerate(rows, 1):
            model = args.model or row.get("model")
            resp = client.post(
                "/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": row["prompt"]}],
                    "stream": False,
                    "temperature": 0.0,
                },
            )
            if resp.status_code != 200:
                print(f"[{i}/{len(rows)}] ERROR HTTP {resp.status_code} model={model}")
                failures += 1
                continue
            content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
            score = similarity(row["response"] or "", content or "")
            status = "OK " if score >= args.threshold else "REGRESSION?"
            if score < args.threshold:
                failures += 1
            print(f"[{i}/{len(rows)}] {status} sim={score:.2f} model={model} "
                  f"prompt={row['prompt'][:60]!r}")

    print(f"\n{len(rows) - failures}/{len(rows)} passed (threshold {args.threshold})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
