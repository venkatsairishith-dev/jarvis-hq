"""Will this month end inside the Oracle Always Free pool? A $0 bill does not say: the cost data lags a day and the allowance is counted in HOURS.

    python jarvis/scripts/oci_free_pool.py --used-ocpu-h 1542 --used-gb-h 9255 --ocpus 3 --gb 18 [--storage-gb 200] [--backups 1]

Give it this month's A1 usage in hours (OCI usage-api, query-type USAGE) and what runs now. It projects to the end of the month and says whether the
pool (1,500 OCPU-hours, 9,000 GB-hours a month = 2 OCPU / 12 GB all month) will be exceeded, when it runs out and a rough cost. The pool figures below
were read from Oracle's own page on the date in FREE_POOL; they change, so the script refuses (exit 3) to give a verdict when that date is over 30
days old: re-read the page, update FREE_POOL and the date, then run it again. The prices are an estimate to be re-checked on Oracle's price list.
Exit 0 inside the pool, 1 over, 2 bad input, 3 the pool figures are stale.
"""
from __future__ import annotations

import argparse
import calendar
import sys
import time
from datetime import date, datetime

FREE_POOL = {"ocpu_hours": 1500.0, "gb_hours": 9000.0, "storage_gb": 200.0, "backups": 5, "verified": "2026-10-24",
             "source": "https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm"}
PRICE = {"ocpu_hour": 0.01, "gb_hour": 0.0015, "note": "estimate: check Oracle's price list before relying on it"}
MAX_AGE_DAYS = 30


def stale(today: date, pool: dict = FREE_POOL) -> bool:
    return (today - date.fromisoformat(pool["verified"])).days > MAX_AGE_DAYS


def project(used_ocpu_h: float, used_gb_h: float, ocpus: float, gb: float, now: datetime, pool: dict = FREE_POOL) -> dict:
    """Month-end projection if the VMs keep running at `ocpus` / `gb`."""
    days = calendar.monthrange(now.year, now.month)[1]
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
    hours_left = max(0.0, (end - now).total_seconds() / 3600)
    end_ocpu, end_gb = used_ocpu_h + ocpus * hours_left, used_gb_h + gb * hours_left
    over_ocpu, over_gb = max(0.0, end_ocpu - pool["ocpu_hours"]), max(0.0, end_gb - pool["gb_hours"])
    runs_out = []
    if ocpus > 0:
        runs_out.append((pool["ocpu_hours"] - used_ocpu_h) / ocpus)
    if gb > 0:
        runs_out.append((pool["gb_hours"] - used_gb_h) / gb)
    first = min(runs_out) if runs_out else float("inf")
    return {"days_in_month": days, "hours_left": round(hours_left, 1), "month_end_ocpu_h": round(end_ocpu, 1), "month_end_gb_h": round(end_gb, 1),
            "over_ocpu_h": round(over_ocpu, 1), "over_gb_h": round(over_gb, 1), "over": over_ocpu > 0 or over_gb > 0,
            "pool_runs_out_in_h": None if first == float("inf") else round(first, 1),
            "est_cost": round(over_ocpu * PRICE["ocpu_hour"] + over_gb * PRICE["gb_hour"], 2)}


def steady_state_ok(ocpus: float, gb: float, pool: dict = FREE_POOL) -> bool:
    """Running at this size ALL month (744 h) stays inside the pool?"""
    return ocpus * 744 <= pool["ocpu_hours"] and gb * 744 <= pool["gb_hours"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--used-ocpu-h", type=float, required=True)
    ap.add_argument("--used-gb-h", type=float, required=True)
    ap.add_argument("--ocpus", type=float, required=True, help="OCPUs of all A1 VMs together, now")
    ap.add_argument("--gb", type=float, required=True, help="GB of memory of all A1 VMs together, now")
    ap.add_argument("--storage-gb", type=float, default=0)
    ap.add_argument("--backups", type=int, default=0)
    a = ap.parse_args(argv)
    if min(a.used_ocpu_h, a.used_gb_h, a.ocpus, a.gb, a.storage_gb) < 0:
        print("numbers must not be negative", file=sys.stderr)
        return 2
    if stale(date.today()):
        print(f"STALE: the pool figures were read on {FREE_POOL['verified']}. Re-read {FREE_POOL['source']}, update FREE_POOL, then run again.")
        return 3
    r = project(a.used_ocpu_h, a.used_gb_h, a.ocpus, a.gb, datetime.now())
    p = FREE_POOL
    print(f"pool (Oracle, read {p['verified']}): {p['ocpu_hours']:.0f} OCPU-h and {p['gb_hours']:.0f} GB-h a month; storage {p['storage_gb']:.0f} GB, {p['backups']} backups")
    print(f"used so far: {a.used_ocpu_h:.0f} OCPU-h, {a.used_gb_h:.0f} GB-h; running now: {a.ocpus:g} OCPU / {a.gb:g} GB for {r['hours_left']} more hours")
    out = r["pool_runs_out_in_h"]
    when = "no running VMs" if out is None else (f"the pool was used up {-out:.0f} h ago" if out < 0 else f"the pool runs out in {out:.0f} h")
    print(f"month end: {r['month_end_ocpu_h']} OCPU-h, {r['month_end_gb_h']} GB-h; {when}")
    bad = r["over"] or a.storage_gb > p["storage_gb"] or a.backups > p["backups"] or not steady_state_ok(a.ocpus, a.gb)
    if r["over"]:
        print(f"OVER the pool by {r['over_ocpu_h']} OCPU-h and {r['over_gb_h']} GB-h this month; rough cost ${r['est_cost']} ({PRICE['note']})")
    if not steady_state_ok(a.ocpus, a.gb):
        print(f"NOT SUSTAINABLE: {a.ocpus:g} OCPU / {a.gb:g} GB all month is {a.ocpus * 744:.0f} OCPU-h and {a.gb * 744:.0f} GB-h; the pool covers 2 OCPU / 12 GB")
    if a.storage_gb > p["storage_gb"] or a.backups > p["backups"]:
        print("storage or backups over the free allowance")
    print("INSIDE the free pool" if not bad else "NOT free: change the size or accept the cost")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
