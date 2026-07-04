---
description: Identify a hash and plan/run cracking (CTF / authorized creds)
argument-hint: <hash or /path/to/hashes>
---

Hash(es): **$ARGUMENTS**

For your own/CTF/authorized-engagement hashes only:
1. Identify the hash type (length, charset, format markers — MD5/SHA/NTLM/bcrypt/$y$/etc.). Give the hashcat mode (`-m`) and john format.
2. Recommend attack order: wordlist (rockyou) → rules → mask → brute, with rationale.
3. Provide the exact `hashcat`/`john` command lines.
4. If the tools + wordlist are present locally and it's small, run it and report cracked results.

Note realistic time cost for slow hashes (bcrypt/argon2) and stop if it's not tractable.
