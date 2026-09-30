"""Command and path safety classification.

Three verdicts:
  ALLOW   - run it.
  CONFIRM - a human must approve in the UI (or via the CLI) before it runs.
  BLOCK   - never run, no confirmation offered.

A compound command is split on shell operators and each segment is classified
separately; the strictest verdict wins. This is a guard rail for an assistant
working in a repo you chose, not a sandbox: it raises the cost of an accident,
it does not contain a determined adversary. Run Forge only on repos you trust.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

class Verdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    BLOCK = "block"


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    segment: str = ""

    @property
    def blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK

    @property
    def needs_confirm(self) -> bool:
        return self.verdict is Verdict.CONFIRM

    def to_dict(self) -> dict:
        return {"verdict": self.verdict.value, "reason": self.reason, "segment": self.segment}


# --- never, under any circumstances ---------------------------------------
BLOCK_RULES: list[tuple[str, str]] = [
    (r"\b(mkfs(\.\w+)?|fdisk|parted|wipefs|shred|blkdiscard)\b", "disk formatting or wiping"),
    (r"\bdd\b[^\n]*\bof=\s*/dev/", "raw write to a block device"),
    (r">\s*/dev/(sd|nvme|hd|disk)", "redirect onto a block device"),
    (r":\s*\(\s*\)\s*\{.*\}\s*;\s*:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b", "host power state change"),
    (r"\bkill\s+(-9\s+)?1\b", "killing PID 1"),
    (r"\bsudo\b", "privilege escalation"),
    (r"\bsu\s+(-|root)\b", "privilege escalation"),
    (r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b", "piping a download into a shell"),
    (r"\bgit\s+push\b", "pushing to a remote (Forge never pushes; do it yourself)"),
    (r"\b(npm|yarn|pnpm)\s+publish\b", "publishing a package"),
    (r"\btwine\s+upload\b", "publishing a package"),
    (r"\bcargo\s+publish\b", "publishing a package"),
    # Credential exfiltration: reading a secret and sending it somewhere.
    (r"(cat|base64|xxd|tar|scp|rsync|openssl)\b[^\n]*"
     r"(\.ssh/id_|\.aws/credentials|\.config/gcloud|\.netrc|\.kube/config|id_rsa|id_ed25519)",
     "reading private keys or cloud credentials"),
    (r"\b(printenv|env|set)\b[^\n]*\|\s*(curl|wget|nc|ncat|netcat)\b",
     "piping the environment to the network"),
    (r"\b(curl|wget)\b[^\n]*(ANTHROPIC_API_KEY|AWS_SECRET|SECRET_KEY|PRIVATE_KEY)",
     "sending a credential over the network"),
    (r"\bnc\b\s+(-[a-z]*e[a-z]*\s)", "netcat reverse shell"),
    (r"\bchmod\s+(-R\s+)?(777|a\+rwx)\s+/(\s|$)", "world-writable filesystem root"),
    (r"\bcrontab\b", "modifying scheduled jobs"),
    (r"\bhistory\s+-c\b", "clearing shell history"),
    (r"\bgit\s+.*\bfilter-(branch|repo)\b", "rewriting repository history"),
]

# --- destructive but legitimate: ask a human ------------------------------
CONFIRM_RULES: list[tuple[str, str]] = [
    (r"\brm\b\s+(-[a-zA-Z]*\s+)*", "deletes files"),
    (r"\bfind\b[^\n]*-delete\b", "bulk delete"),
    (r"\bfind\b[^\n]*-exec\s+rm\b", "bulk delete"),
    (r"\bgit\s+reset\s+(--hard|--merge)\b", "discards working tree changes"),
    (r"\bgit\s+checkout\s+(--\s+\.|\.\s*$)", "discards working tree changes"),
    (r"\bgit\s+restore\b", "discards working tree changes"),
    (r"\bgit\s+clean\b", "deletes untracked files"),
    (r"\bgit\s+(rebase|cherry-pick|revert|merge)\b", "rewrites or moves history"),
    (r"\bgit\s+branch\s+-[dD]\b", "deletes a branch"),
    (r"\bgit\s+stash\s+(drop|clear|pop)\b", "discards stashed work"),
    (r"\bgit\s+commit\b", "creates a commit (use the Create commit action instead)"),
    (r"\b(truncate|dd)\b", "truncates or overwrites files"),
    (r"\b(mv|cp)\s+(-[a-zA-Z]*\s+)*[^\n]*\s+/(etc|usr|var|bin|lib|opt)\b",
     "writes outside the project"),
    (r"\b(apt|apt-get|yum|dnf|pacman|brew)\s+(install|remove|purge|upgrade)\b",
     "changes system packages"),
    (r"\b(npm|yarn|pnpm)\s+(install|add|remove|uninstall|ci|update)\b",
     "changes project dependencies"),
    (r"\bpip3?\s+(install|uninstall)\b", "changes installed Python packages"),
    (r"\b(cargo|go|composer|bundle|gem)\s+(install|add|get|remove)\b",
     "changes project dependencies"),
    (r"\b(chmod|chown)\s+-R\b", "recursive permission change"),
    (r"\bdocker\b", "container operations"),
    (r"\b(kill|pkill|killall)\b", "terminates processes"),
    (r"\bmake\s+(install|uninstall|distclean)\b", "installs or removes files system-wide"),
    (r"\bssh\b", "connects to a remote host"),
    (r"\b(curl|wget)\b[^\n]*\s-o+\b", "downloads a file to disk"),
    (r">\s*/", "redirects output to an absolute path"),
]

_RM_RECURSIVE = re.compile(r"\brm\b(?P<flags>(\s+-{1,2}[a-zA-Z-]+)*)\s+(?P<rest>.*)", re.I)
_SPLIT = re.compile(r"&&|\|\||;|\n|\|")
_BLOCK_COMPILED = [(re.compile(p, re.I), why) for p, why in BLOCK_RULES]
_CONFIRM_COMPILED = [(re.compile(p, re.I), why) for p, why in CONFIRM_RULES]


def _check_rm(segment: str, root: Path | None) -> Decision | None:
    """A recursive `rm` is BLOCK when any target lies outside the project root.

    Inside the root it is CONFIRM: deleting build/ is a normal thing to ask for.
    """
    m = _RM_RECURSIVE.match(segment.strip())
    if not m:
        return None
    flags = (m.group("flags") or "").lower()
    recursive = any(c in flags.replace("--recursive", "r") for c in "r") or "-R" in (m.group("flags") or "")
    targets = [t for t in m.group("rest").split() if not t.startswith("-")]
    for target in targets:
        t = target.strip("'\"")
        if t in {"/", "/*", "~", "~/", "$HOME", "${HOME}", ".", "./", "*"} and recursive:
            return Decision(Verdict.BLOCK, "recursive delete of a root, home or the whole tree", segment)
        outside = t.startswith("~") or t.startswith("$HOME") or t.startswith("${HOME}")
        if not outside and t.startswith("/"):
            if root is None:
                outside = True
            else:
                try:
                    resolved = Path(t).resolve()
                    r = root.resolve()
                    outside = resolved != r and r not in resolved.parents
                except OSError:
                    outside = True
        if outside:
            return Decision(Verdict.BLOCK, f"delete targets a path outside the project: {t}", segment)
    return None


def classify_command(command: str, root: Path | None = None) -> Decision:
    """Classify a shell command string. Strictest verdict across all segments wins."""
    if not command or not command.strip():
        return Decision(Verdict.BLOCK, "empty command")
    if len(command) > 4000:
        return Decision(Verdict.BLOCK, "command is implausibly long")

    # Whole-string pass first: catches patterns that span a pipe (download | sh).
    for rx, why in _BLOCK_COMPILED:
        if rx.search(command):
            return Decision(Verdict.BLOCK, why, command.strip())

    worst = Decision(Verdict.ALLOW, "no destructive pattern matched", command.strip())
    for raw in _SPLIT.split(command):
        seg = raw.strip()
        if not seg:
            continue
        for rx, why in _BLOCK_COMPILED:
            if rx.search(seg):
                return Decision(Verdict.BLOCK, why, seg)
        rm_verdict = _check_rm(seg, root)
        if rm_verdict is not None:
            return rm_verdict
        for rx, why in _CONFIRM_COMPILED:
            if rx.search(seg):
                worst = Decision(Verdict.CONFIRM, why, seg)
                break
    return worst


# --- paths ----------------------------------------------------------------
PROTECTED_DIRS = {".git"}


def resolve_in_root(root: Path, candidate: str) -> Path:
    """Resolve `candidate` under `root`, rejecting traversal and symlink escapes.

    Raises PermissionError if the result would land outside the project root.
    """
    root = root.resolve()
    p = Path(candidate).expanduser()
    p = p if p.is_absolute() else root / p
    # resolve() collapses .. and follows existing symlinks; strict=False allows new files.
    resolved = p.resolve()
    if resolved != root and root not in resolved.parents:
        raise PermissionError(f"path escapes the project root: {candidate}")
    return resolved


def check_write_path(root: Path, candidate: str) -> Path:
    """Resolve for writing. Also refuses to touch git internals."""
    resolved = resolve_in_root(root, candidate)
    rel = resolved.relative_to(root.resolve())
    if rel.parts and rel.parts[0] in PROTECTED_DIRS:
        raise PermissionError(f"refusing to write inside {rel.parts[0]}/")
    return resolved
