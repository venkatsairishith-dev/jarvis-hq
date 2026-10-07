---
name: oracle-delete
description: Delete Oracle resources safely: list, offer backup, delete one named resource at a time.
---

1. List instances, boot volumes, block volumes, networks, reserved IPs and backups in the named compartment, with names and sizes.
2. NEVER touch hermes-agent-vm-2 or anything outside the compartment.
3. Offer a backup; wait for the answer.
4. Get a yes naming the exact resource. Delete one at a time; abort unless exactly one exact-name match.
5. Re-list and show the result. Report cost afterwards (cost-check).

