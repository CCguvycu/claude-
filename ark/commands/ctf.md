---
description: CTF solver mode — triage a challenge and drive to the flag
argument-hint: <category + description, or a file/URL>
---

Challenge: **$ARGUMENTS**

Go into CTF-solver mode:
1. Classify: web / pwn / crypto / rev / forensics / osint / misc.
2. Run first-look triage (file, strings, binwalk, exiftool, checksec, `curl -sv`, source view — whatever fits) using available tools.
3. Form 2-3 hypotheses for where the flag hides and the technique to get it.
4. Execute the most likely path; iterate on results.
5. When you get it, print the flag clearly (format like `flag{...}`/`CTF{...}`).

Be fast and hands-on. Use the shell. Show your work so the operator learns the method.
