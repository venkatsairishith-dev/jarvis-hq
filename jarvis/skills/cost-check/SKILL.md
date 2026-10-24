---
name: cost-check
description: Check that Oracle spend is $0 after a build phase. Use at the end of every phase.
---

1. Run: python -I D:\jarvis\hq\scripts\oci_cost.py (OCI usage-api, this month, by service; reads D:\jarvis-secrets\oci\config via the CLI, prints no secrets). Billing lags up to a day.
2. Show cost per service. Total must be $0.
3. Confirm budget jarvis-1usd is ACTIVE.
4. Record the result in vault/daily/<date>.md. If any cost is above $0 or a resource is not Always Free-eligible, stop and tell the user.
Never print secrets. Use D:\jarvis-secrets\oci\config through the OCI CLI only.

Free is not the same as $0 (added 2026-10-24): billing lags a day and the free allowance is counted in hours.
5. Read the Always Free page TODAY (docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm): A1 OCPU-hours and GB-hours a month, storage GB, backups. Quote them with the date.
6. Get this month's A1 usage in HOURS (usage-api, query-type USAGE) and the current OCPU/GB of every A1 VM, then run: python scripts/oci_free_pool.py --used-ocpu-h <n> --used-gb-h <n> --ocpus <total now> --gb <total now>. If it says the month will end over the pool, stop and tell the user, even when the cost shows $0.
