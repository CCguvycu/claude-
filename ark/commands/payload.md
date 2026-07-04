---
description: Craft a payload/one-liner for a lab/CTF/authorized test
argument-hint: <type + context, e.g. "reverse shell bash 10.0.0.1:4444">
---

Payload request: **$ARGUMENTS**

For authorized testing, CTF, or the operator's own lab only. Confirm scope if it reads like a real third party.

Produce:
- The requested payload/one-liner (reverse/bind shell, listener, upgrade to PTY, encoder, etc.) in the right language/format for the context.
- Alternatives across shells/langs (bash, nc, python, PowerShell, php) when relevant.
- The matching listener command (e.g. `nc -lvnp`, `pwncat`, `msfconsole` handler).
- Any encoding needed to survive the delivery channel (URL/base64/no-bad-chars).

Keep it copy-pasteable. One line on OPSEC/noise if relevant. No real-world malware or self-propagation.
