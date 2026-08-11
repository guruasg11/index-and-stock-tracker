#!/usr/bin/env python3
"""Refresh the local NSE end-of-day store.

    python build_data.py                 # incremental, 400-day window
    python build_data.py --days 500      # wider backfill (first run)
    python build_data.py --no-constituents
    python build_data.py --until 2026-08-08

Run after 18:30 IST on a trading day. The first run downloads roughly one
file pair per trading day and takes 15-40 minutes; later runs fetch only
the missing days.
"""

import argparse
import json
import logging
import sys
from datetime import date

from nsedata.build import update


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=400,
                   help="calendar days of history to keep (default 400)")
    p.add_argument("--no-constituents", action="store_true",
                   help="skip re-downloading index membership lists")
    p.add_argument("--until", type=str, default=None,
                   help="build up to this date (YYYY-MM-DD), default today")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    until = date.fromisoformat(args.until) if args.until else None

    try:
        meta = update(
            days_back=args.days,
            refresh_constituents=not args.no_constituents,
            until=until,
        )
    except Exception as exc:
        logging.error("Build failed: %s", exc)
        return 1

    print(json.dumps(meta, indent=2))
    if meta["constituent_lists_missing"]:
        print(
            "\nConstituent lists not resolved for: "
            + ", ".join(meta["constituent_lists_missing"])
            + "\nAdd the correct filename to CONSTITUENT_FILE_OVERRIDES in "
              "config/universe.py.",
            file=sys.stderr,
        )
    if meta["indices_not_found_in_nse_file"]:
        print(
            "\nIndices absent from NSE's daily file: "
            + ", ".join(meta["indices_not_found_in_nse_file"])
            + "\nCheck data/index_names_seen.csv for the exact published name "
              "and add an alias in config/universe.py.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
