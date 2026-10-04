"""NEXUS Workspace Authorization — explicit authorized roots, protected paths, approval classification.

Every consequential local operation (filesystem writes, process execution)
must name an explicitly authorized workspace root. This module is the single
source of truth for:

- which roots are authorized (``get_authorized_roots``)
- whether a path is inside an authorized root (``is_authorized_workspace``)
- which paths are never writable/executable regardless of roots (``is_protected_path``)
- which operations need human approval (``classify_consequential``)

Truth-boundary note: this module decides ALLOW / REQUIRES_APPROVAL / DENY.
It never executes anything and never observes anything. Execution evidence
comes only from connectors; verification only from the VerificationAgent.

Environment overrides (operator-owned, never model-supplied):

- ``NEXUS_WORKSPACE_ROOTS`` — extra authorized roots, ``os.pathsep``-separated.
- ``NEXUS_PROTECTED_PATHS`` — extra protected paths, ``os.pathsep``-separated.
- ``NEXUS_ALLOWED_COMMANDS`` — extra allowlisted command binaries, comma-separated.
- ``NEXUS_AUTO_APPROVE`` — when ``"1"``, REQUIRES_APPROVAL degrades to an
  explicitly-recorded ALLOW (operator opt-in for autonomous demos/tests).
  DENY is never degraded.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Authorized roots
# ---------------------------------------------------------------------------

def _runtime_repo_root() -> str:
    """The NEXUS repository root (parent of this module's package)."""
    try:
        return str(Path(__file__).resolve().parent.parent)
    except (OSError, ValueError):
        return ""


def _candidate_default_roots() -> list[str]:
    candidates = [_runtime_repo_root(), os.getcwd(), tempfile.gettempdir()]
    # The documented demo workspace; authorized only when it actually exists
    # so a missing drive never becomes a silent scope expansion.
    for extra in (r"E:\Projects", r"E:\NexusTest"):
        candidates.append(extra)
    return candidates


def get_authorized_roots() -> list[str]:
    """Return the resolved, existing, authorized workspace roots.

    Roots come from explicit operator configuration (``NEXUS_WORKSPACE_ROOTS``)
    plus conservative defaults (repository root, process cwd, system temp dir).
    Only existing directories are returned — a configured-but-missing root
    authorizes nothing.
    """
    roots: list[str] = []
    seen: set[str] = set()
    candidates: list[str] = []
    configured = os.getenv("NEXUS_WORKSPACE_ROOTS", "") or ""
    for chunk in configured.split(os.pathsep):
        if chunk.strip():
            candidates.append(chunk.strip())
    candidates.extend(_candidate_default_roots())
    for candidate in candidates:
        try:
            resolved = str(Path(candidate).expanduser().resolve())
        except (OSError, ValueError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            if Path(resolved).is_dir():
                roots.append(resolved)
        except (OSError, ValueError):
            continue
    return roots


def resolve_within_root(root: str, target: str) -> str | None:
    """Resolve ``target`` against ``root``; return None when it escapes.

    Relative targets resolve against the root (never the process cwd).
    Symlinks are resolved before the containment check so link escapes fail.
    Non-existent targets are allowed (writes create new paths) as long as the
    resolved location stays inside the root.
    """
    if not target or not isinstance(target, str):
        return None
    try:
        raw = Path(target.strip()).expanduser()
        base = Path(root).resolve()
        candidate = (base / raw).resolve() if not raw.is_absolute() else raw.resolve()
    except (OSError, ValueError):
        return None
    try:
        if candidate == base or base in candidate.parents:
            return str(candidate)
    except (OSError, ValueError):
        return None
    return None


def is_authorized_workspace(path: str) -> str | None:
    """Return the authorized root containing ``path``, or None.

    The path itself may not exist yet (writes create new paths); what must
    exist is the authorizing root. An empty/invalid path authorizes nothing.
    """
    if not path or not isinstance(path, str):
        return None
    for root in get_authorized_roots():
        if resolve_within_root(root, path) is not None:
            return root
    return None


# ---------------------------------------------------------------------------
# Protected paths — never writable/executable, even inside an authorized root
# ---------------------------------------------------------------------------

def _builtin_protected() -> list[str]:
    protected: list[str] = []
    if os.name == "nt":
        for var in ("WINDIR", "SYSTEMROOT", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"):
            value = os.getenv(var, "")
            if value:
                protected.append(value)
        # Drive roots themselves (e.g. ``E:\\``): a workspace must be a
        # directory below the root, never the whole drive.
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            protected.append(f"{letter}:\\")
    else:
        protected.extend(["/", "/etc", "/proc", "/sys", "/dev",
                          "/bin", "/sbin", "/usr", "/boot", "/root",
                          "/lib", "/lib64"])
    configured = os.getenv("NEXUS_PROTECTED_PATHS", "") or ""
    for chunk in configured.split(os.pathsep):
        if chunk.strip():
            protected.append(chunk.strip())
    return protected


def is_protected_path(path: str) -> str | None:
    """Return the matching protected prefix for ``path``, or None.

    Matches on resolved path identity or containment. Any path containing a
    ``.git`` segment is protected: consequential writes must never touch
    version-control internals directly (git mutation has no connector).
    """
    if not path or not isinstance(path, str):
        return None
    try:
        resolved = Path(path.strip()).expanduser().resolve()
    except (OSError, ValueError):
        return None
    if ".git" in resolved.parts:
        return ".git internals"
    resolved_str = str(resolved)
    for candidate in _builtin_protected():
        try:
            prefix = str(Path(candidate).expanduser().resolve())
        except (OSError, ValueError):
            continue
        if os.name == "nt":
            lower_resolved = resolved_str.lower()
            lower_prefix = prefix.lower()
            if lower_resolved == lower_prefix:
                return prefix
            # A drive root (``C:\\``) protects only the root directory
            # itself — never the whole drive. Any other protected prefix
            # protects the directory and everything beneath it.
            if len(prefix.rstrip("\\")) <= 3 and prefix.rstrip("\\").endswith(":"):
                continue
            if lower_resolved.startswith(lower_prefix.rstrip("\\") + "\\"):
                return prefix
        else:
            if resolved_str == prefix:
                return prefix
            # The filesystem root (``/``) protects only itself, not the
            # whole machine; every other prefix protects its subtree.
            if prefix.rstrip("/") == "":
                continue
            if resolved_str.startswith(prefix.rstrip("/") + "/"):
                return prefix
    return None


# ---------------------------------------------------------------------------
# Command allowlist
# ---------------------------------------------------------------------------

def _default_allowed_binaries() -> set[str]:
    names = {"pytest"}
    try:
        names.add(Path(sys.executable).name.lower())
    except (OSError, ValueError):
        pass
    names.update({"python", "python3", "python.exe"})
    return names


def get_allowed_binaries() -> set[str]:
    """Binary names permitted for unattended ``process.command.run``."""
    allowed = _default_allowed_binaries()
    configured = os.getenv("NEXUS_ALLOWED_COMMANDS", "") or ""
    for chunk in configured.split(","):
        if chunk.strip():
            allowed.add(chunk.strip().lower())
    try:
        allowed.add(str(Path(sys.executable).resolve()).lower())
    except (OSError, ValueError):
        pass
    return allowed


def is_allowlisted_command(argv: list[str]) -> bool:
    """True when ``argv[0]`` names an allowlisted binary (no shell, ever)."""
    if not argv:
        return False
    first = str(argv[0] or "").strip()
    if not first:
        return False
    try:
        base = Path(first).name.lower()
    except (OSError, ValueError):
        return False
    allowed = get_allowed_binaries()
    return base in allowed or first.lower() in allowed


# ---------------------------------------------------------------------------
# Approval classification — the human-approval boundary (Phase H)
# ---------------------------------------------------------------------------

WRITE_CAPABILITIES = frozenset({
    "filesystem.directory.create",
    "filesystem.file.create",
    "filesystem.file.write",
    "filesystem.file.update",
    "filesystem.write",
    "filesystem.create",
    "filesystem.patch",
})

DELETE_CAPABILITIES = frozenset({
    "filesystem.file.delete",
})

PROCESS_CAPABILITIES = frozenset({
    "process.command.run",
    "project.test.run",
    "project.build.run",
})

CONSEQUENTIAL_CAPABILITIES = WRITE_CAPABILITIES | DELETE_CAPABILITIES | PROCESS_CAPABILITIES


def is_auto_approve() -> bool:
    """Explicit operator opt-in that degrades REQUIRES_APPROVAL to ALLOW."""
    return (os.getenv("NEXUS_AUTO_APPROVE", "") or "").strip() == "1"


def classify_consequential(
    *,
    capability: str,
    workspace: str = "",
    target: str = "",
    argv: list[str] | None = None,
) -> tuple[str, str]:
    """Classify one consequential operation.

    Returns ``(decision, reason)`` where decision is one of
    ``ALLOW`` | ``REQUIRES_APPROVAL`` | ``DENY``.

    - Unrecognized capabilities are DENIED (never silently allowed).
    - A workspace outside the authorized roots is DENIED.
    - A protected target is DENIED.
    - Deletes and non-allowlisted commands REQUIRE_APPROVAL (auto-approvable
      only via explicit ``NEXUS_AUTO_APPROVE=1``, recorded in the reason).
    - Bounded creates/writes and fixed test/build runners are ALLOWed inside
      an authorized workspace: containment is the authorization boundary.
    """
    cap = (capability or "").strip()
    if cap not in CONSEQUENTIAL_CAPABILITIES:
        return ("DENY", f"capability {cap!r} is not a governed consequential capability")
    root = is_authorized_workspace(workspace) if workspace else None
    if root is None:
        return ("DENY", f"workspace {workspace!r} is outside the authorized roots {get_authorized_roots()}")
    resolved_target = resolve_within_root(root, target) if target else root
    if resolved_target is None:
        return ("DENY", f"target {target!r} escapes authorized workspace root {root}")
    protected = is_protected_path(resolved_target)
    if protected is not None:
        return ("DENY", f"target {resolved_target!r} is inside protected location {protected}")
    if cap in DELETE_CAPABILITIES:
        if is_auto_approve():
            return ("ALLOW", "delete auto-approved via NEXUS_AUTO_APPROVE=1 (operator opt-in, recorded)")
        return ("REQUIRES_APPROVAL", f"deleting {resolved_target!r} requires human approval by default")
    if cap == "process.command.run":
        if not argv:
            return ("DENY", "process.command.run requires an explicit argv list (shell strings are never executed)")
        if not is_allowlisted_command(argv):
            if is_auto_approve():
                return ("ALLOW", f"command {argv[0]!r} auto-approved via NEXUS_AUTO_APPROVE=1 (operator opt-in, recorded)")
            return ("REQUIRES_APPROVAL", f"command {argv[0]!r} is not allowlisted; human approval required")
        return ("ALLOW", f"command {argv[0]!r} is allowlisted and workspace-bound to {root}")
    # Bounded creates/writes/updates and fixed test/build runners.
    return ("ALLOW", f"{cap} inside authorized workspace root {root}")
