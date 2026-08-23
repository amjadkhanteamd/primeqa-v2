"""Manual CLI for the Phase 2.1 browser-worker spike.

Usage:
    python -m primeqa.browser_worker --url https://example.com \
        [--url https://other.example] [--json-out /tmp/spike.json]
"""

from __future__ import annotations

import argparse
import json
import urllib.request

from primeqa.browser_worker.spike import scan_page


def _egress_ip() -> str:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
            return resp.read().decode("ascii", errors="replace").strip()
    except Exception:  # noqa: BLE001 — diagnostics only; never crash the run
        return "UNKNOWN"


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m primeqa.browser_worker")
    parser.add_argument(
        "--url", action="append", required=True,
        help="page to scan; repeatable",
    )
    parser.add_argument(
        "--json-out", metavar="PATH", default=None,
        help="write the full JSON results (a list, one entry per url) here",
    )
    args = parser.parse_args()

    print(f"EGRESS_IP={_egress_ip()}")

    results = []
    for url in args.url:
        result = scan_page(url)
        result.pop("screenshot_png", None)   # bytes; the size stays in screenshot_bytes
        results.append(result)
        total_ms = round(sum(result["timings_ms"].values()), 1)
        obs = result.get("engine_observations")
        if obs is not None:
            counts = (
                f"violations={obs['violations_count']} "
                f"passes={obs['passes_count']} "
                f"incomplete={obs['incomplete_count']}"
            )
        else:
            counts = "observations=none"
        print(f"{url} status={result['status']} total_ms={total_ms} {counts}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"wrote {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
