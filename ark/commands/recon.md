---
description: Recon methodology + live enumeration for an AUTHORIZED target
argument-hint: <target host/domain/IP or scope>
---

Operator wants reconnaissance on: **$ARGUMENTS**

First confirm this is in authorized scope (their asset, engagement scope, bug-bounty program, CTF, or lab). If unclear, ask once, then proceed.

Then run passive → active recon and report findings:
1. Passive: whois, DNS records (A/AAAA/MX/TXT/NS), subdomain discovery, tech fingerprinting, exposed metadata. Use available tools where installed.
2. Active (if in scope): host discovery, top-port sweep, service/version detection, banner grabbing.
3. Summarize: attack surface, interesting services, likely next moves.

Show every command you run. Flag anything noisy/destructive before running it.
