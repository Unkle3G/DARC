"""Command line entry point.

    liver-intel discover [--source S]     find candidate feeds (writes registry)
    liver-intel verify   [--source S]     probe candidates, promote to verified
    liver-intel status                    registry / roster coverage / calendar
    liver-intel daily    [--since D]      collect, grade, write the daily report
    liver-intel weekly                    drain the pool into the Friday digest
    liver-intel backfill --since D        historical sweep (acceptance testing)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import date, timedelta

from .config import Settings
from .conference import Calendar
from .discover import discover
from .domain_map import load as load_domain_map
from .feeds import Registry
from .http import Fetcher
from .models import today_iso
from .pipeline import run_daily, run_weekly
from .store import Store
from .verify import verify_registry


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings()
    if getattr(args, "allow_unverified", False):
        settings = replace(settings, allow_unverified=True)
    if getattr(args, "cap", None):
        settings = replace(settings, daily_cap=int(args.cap))
    return settings


def cmd_discover(args: argparse.Namespace) -> int:
    registry = Registry.load()
    fetcher = Fetcher()
    roots = registry.discover_roots(source=args.source)
    if not roots:
        print("no discovery roots matched", file=sys.stderr)
        return 1
    found = 0
    for root in roots:
        candidates = discover(root, fetcher)
        for candidate in candidates:
            registry.upsert(candidate)
            found += 1
        print(f"{root.id}: {len(candidates)} candidate(s)")
    registry.save()
    print(f"\n{found} candidate(s) written as status=unverified. "
          f"Run `liver-intel verify` before they are used.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    registry = Registry.load()
    fetcher = Fetcher()
    results = verify_registry(registry, fetcher, only=args.source,
                              include_verified=args.recheck)
    registry.save()
    ok = sum(1 for r in results if r.ok)
    for result in results:
        mark = "ok  " if result.ok else "FAIL"
        print(f"{mark} {result.feed.status:10s} {result.feed.id}: "
              f"{result.feed.note or result.reason}")
    print(f"\n{ok}/{len(results)} candidate(s) verified")
    print(json.dumps(registry.counts(), indent=2))
    return 0 if ok or not results else 1


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    registry = Registry.load()
    dm = load_domain_map()
    calendar = Calendar.load()

    print("registry:", json.dumps(registry.counts()))
    coverage, missing = registry.coverage(c.name for c in dm.companies)
    print(f"roster coverage (verified feed per company): {coverage:.0%} "
          f"({len(missing)} without one)")
    if missing and args.verbose:
        for name in missing:
            print(f"  - {name}")
    print(f"lines: {len(dm.lines)} | companies: {len(dm.companies)} | "
          f"KOL verified: {len(dm.verified_kols)}, unverified: {len(dm.unverified_kols)}")
    if dm.unverified_kols:
        print("  unverified KOL entries are not used for matching until confirmed: "
              + ", ".join(k.name for k in dm.unverified_kols))
    for warning in calendar.warnings():
        print(f"warning: {warning}")
    with Store(settings.db_path) as store:
        print(f"tracked NCTs: {store.known_nct_count()} | "
              f"weekly pool: {len(store.drain_weekly())}")
    if coverage < 0.8:
        print("\nT1 acceptance bar is >=80% roster coverage; run discover + verify.")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    settings = _settings(args)
    result = run_daily(settings, today=args.date, since=args.since,
                       only=args.source.split(",") if args.source else None,
                       use_llm=not args.no_llm, write=not args.dry_run)
    print(f"collected {result.collected}; daily {len(result.daily)} "
          f"{json.dumps(result.counts)}; weekly pool +{len(result.weekly)}")
    for note in result.notes:
        print(f"  note: {note}")
    if result.report_path:
        print(f"wrote {result.report_path}")
        print(f"wrote {result.json_path}")
    return 0


def cmd_weekly(args: argparse.Namespace) -> int:
    settings = _settings(args)
    result = run_weekly(settings, today=args.date, limit=args.limit,
                        write=not args.dry_run)
    print(f"weekly digest: {len(result.weekly)} item(s)")
    if result.report_path:
        print(f"wrote {result.report_path}")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    settings = _settings(args)
    since = args.since or (date.fromisoformat(today_iso(settings.report_tz))
                           - timedelta(days=30)).isoformat()
    result = run_daily(settings, since=since,
                       only=args.source.split(",") if args.source else None,
                       use_llm=not args.no_llm, write=not args.dry_run)
    print(f"backfill since {since}: collected {result.collected}, "
          f"daily {len(result.daily)}, weekly {len(result.weekly)}")
    for note in result.notes:
        print(f"  note: {note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="liver-intel",
                                     description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="find candidate feeds from discovery roots")
    p.add_argument("--source")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("verify", help="probe candidates and promote them")
    p.add_argument("--source", help="source id, task id (T1..T8) or feed id")
    p.add_argument("--recheck", action="store_true", help="re-probe verified entries too")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("status", help="registry, roster coverage and calendar state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("daily", help="run the daily collection and report")
    p.add_argument("--date")
    p.add_argument("--since", help="only take items on or after this ISO date")
    p.add_argument("--source", help="comma separated source ids")
    p.add_argument("--cap", type=int)
    p.add_argument("--no-llm", action="store_true",
                   help="skip the significance step, use deterministic rules only")
    p.add_argument("--allow-unverified", action="store_true",
                   help="use registry entries that have not passed verification")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_daily)

    p = sub.add_parser("weekly", help="drain the weekly pool into the digest")
    p.add_argument("--date")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("backfill", help="historical sweep for acceptance testing")
    p.add_argument("--since")
    p.add_argument("--source")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--allow-unverified", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_backfill)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
