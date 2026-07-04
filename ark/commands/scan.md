---
description: Plan/run an nmap-style port & service scan (authorized targets only)
argument-hint: <target> [ports/flags]
---

Target: **$ARGUMENTS**

Confirm authorization (owned/in-scope/CTF/lab). Then:
- Propose the exact `nmap` command(s) for the goal (quick sweep vs full vs UDP vs script scan), explaining the flags.
- If nmap is installed and the target is local/in-scope, run it and parse results.
- Map open ports → services → likely versions → known-vuln leads.
- Recommend the next enumeration step per interesting service.

Prefer `-Pn -sV` for accuracy; call out timing/noise tradeoffs (`-T4`, `--min-rate`).
