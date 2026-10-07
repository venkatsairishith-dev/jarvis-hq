---
name: cost-check
description: Check that Oracle spend is $0 after a build phase. Use at the end of every phase.
---

1. Open Oracle Cost Analysis for the current month (OCI CLI usage-api or console). Billing lags up to a day.
2. Show cost per service. Total must be $0.
3. Confirm budget jarvis-1usd is ACTIVE.
4. Record the result in vault/daily/<date>.md. If any cost is above $0 or a resource is not Always Free-eligible, stop and tell the user.
Never print secrets. Use D:\jarvis-secrets\oci\config through the OCI CLI only.

