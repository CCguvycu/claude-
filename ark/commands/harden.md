---
description: Blue-team — audit and harden a host/service/config
argument-hint: <what to harden, e.g. "this ubuntu box" or "sshd_config">
---

Harden: **$ARGUMENTS**

Blue-team pass:
1. Audit the current state (read configs, check listening services, users, perms, firewall, updates) using the shell/Read.
2. Report findings ranked by risk (critical → low), each with the concrete fix.
3. Provide the exact hardening commands/config diffs (CIS-benchmark-aligned where relevant).
4. Note what to monitor/log to detect attacks against this asset.

Don't apply changes without confirming — show the diff first.
