---
description: Decode/encode/deobfuscate a blob (base64, hex, url, rot, gzip, JWT…)
argument-hint: <the data to work on>
---

Data: **$ARGUMENTS**

Identify the encoding(s) and fully decode, chaining as needed:
- Detect: base64/base32, hex, URL, ROT13/47, binary, gzip/zlib, JWT, ASCII-85, etc.
- Decode all the way down (multi-layer is common in CTF).
- If it's a JWT: decode header+payload, flag `alg:none`/weak secret.
- If it looks encrypted (not just encoded), say so and suggest the crack path.

Show each layer. If you have a shell, use it (base64 -d, xxd, python, jq).
Reverse direction too if the operator asked to ENCODE.
