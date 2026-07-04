---
description: Deep service enumeration checklist for a given port/service
argument-hint: <service/port, e.g. "smb 445" or "http 8080 on 10.0.0.5">
---

Enumerate: **$ARGUMENTS**

Run the standard enumeration playbook for this service and execute what you can:
- Web (80/443/8080): dirb/gobuster/ffuf wordlist bust, headers, robots.txt, tech stack, default creds, known CVEs for the detected stack.
- SMB (445/139): shares, null sessions, users, versions (enum4linux-style).
- SSH (22): version, auth methods, weak-cred candidates.
- DB (3306/5432/1433/27017): version, default/blank creds, exposure.
- SNMP/FTP/RDP/etc.: the appropriate checks.

Output: what you found, what's exploitable, and the single best next move.
Authorized targets only — confirm scope if unclear.
