---
description: Decode/encode/deobfuscate a blob (base64, hex, url, rot, gzip, JWT…)
argument-hint: <the data to work on>
---

Operator wants this decoded: `$ARGUMENTS`

The shell has ALREADY run the common decoders for you — read the outputs below and
report the one that produced meaningful/readable text (or a flag). Do not call any
tool; the answers are already here.

- base64  → !`printf %s '$ARGUMENTS' | base64 -d 2>/dev/null; echo`
- hex      → !`printf %s '$ARGUMENTS' | tr -d ' \n' | xxd -r -p 2>/dev/null; echo`
- url      → !`printf %s '$ARGUMENTS' | sed 's/+/ /g;s/%/\\x/g' | xargs -0 printf '%b' 2>/dev/null; echo`
- rot13    → !`printf %s '$ARGUMENTS' | tr 'A-Za-z' 'N-ZA-Mn-za-m'; echo`
- base32  → !`printf %s '$ARGUMENTS' | base32 -d 2>/dev/null; echo`

Now:
1. State the correctly-decoded value plainly (the readable one). Name which encoding it was.
2. If the decoded value ITSELF still looks encoded (multi-layer is common in CTF),
   decode the next layer with the **Bash** tool and repeat.
3. If it is a JWT (three dot-separated parts), base64-decode the header + payload
   with Bash and flag `alg:none` / a weak secret.
4. If nothing decoded to readable text, it is likely encrypted (not just encoded) —
   say so and suggest the crack path.

To ENCODE instead of decode, use the Bash tool (`base64`, `xxd -p`, …).
