"""
ClaraGate provenance extraction (v1, deterministic, causal).

Extracts task-relevance EVIDENCE from an OpenCode session, incrementally, using
only what has been observed so far. NO up-front repo scan, NO LLM classifier.

Three evidence sources:
  1. imports  — AST-parsed from the CONTENT of files the agent READS
  2. output   — filenames appearing in pytest/error tool output (noise-filtered)
  3. goal     — filenames explicitly named in the user's goal text

All paths are normalized repo-relative (basename-based here for the flat test
repos; a real repo-root normalization is a later refinement).
"""
from __future__ import annotations
import ast, re, os
from typing import Optional, List, Tuple

# --- case-normalization policy (explicit, not silently assumed) ---
# True  = case-sensitive filesystem (Linux/CI/strict macOS): PRESERVE case.
# False = case-insensitive filesystem (Windows/default macOS): FOLD case.
# Default True = conservative: never silently merge two case-distinct files.
_CASE_SENSITIVE = True

def set_case_sensitive(sensitive: bool):
    """Declare the target filesystem's case semantics. Live runtime can detect
    this from the environment; offline it is an explicit configured parameter."""
    global _CASE_SENSITIVE
    _CASE_SENSITIVE = bool(sensitive)

def _fold(s: str) -> str:
    """Apply the case policy to a path key."""
    return s if _CASE_SENSITIVE else s.lower()

# --- path handling ------------------------------------------------------------
# --- explicit per-run identity config (NO process-global guessing) ---
_UNVERIFIED_PREFIX = "\x00UNVERIFIED:"
def is_unverified(cid):
    return isinstance(cid, str) and cid.startswith(_UNVERIFIED_PREFIX)

class ReplayConfig:
    __slots__=("repo_root",)
    def __init__(self, repo_root=None): self.repo_root=repo_root

_ACTIVE_CONFIG = ReplayConfig(repo_root=None)

class attested_root:
    """Context manager: sets the attested repo root for a block and RESETS after,
    so replay B can never inherit replay A's namespace (determinism)."""
    def __init__(self, root): self._root=root; self._prev=None
    def __enter__(self):
        global _ACTIVE_CONFIG
        self._prev=_ACTIVE_CONFIG
        _ACTIVE_CONFIG=ReplayConfig(repo_root=self._root); return self
    def __exit__(self,*a):
        global _ACTIVE_CONFIG
        _ACTIVE_CONFIG=self._prev

def set_attested_repo_root(root):
    """Back-compat shim; prefer `with attested_root(...)`."""
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG=ReplayConfig(repo_root=root)


# repo-root markers: a path segment after which the rest is repo-relative
def _repo_relative(p: str) -> str:
    return _fold(_repo_relative_raw(p))

def _repo_relative_raw(p: str, repo_root=None) -> str:
    """Repo-relative identity. Exact canonicalization against the attested root.
      - ABSOLUTE path + attested root -> exact canon (mapper); escape/outside -> UNVERIFIED.
      - ABSOLUTE path + NO attested root -> UNVERIFIED (fail closed: no namespace to
        strip a machine prefix against).
      - ALREADY-RELATIVE path -> repo-relative as-is (no machine prefix to strip),
        with '..' escapes REJECTED -> UNVERIFIED. (Relative fixtures / git-tree cids.)
    The legacy last-2-segments heuristic is REMOVED entirely."""
    import repo_root_mapper as _M
    root = repo_root if repo_root is not None else getattr(_ACTIVE_CONFIG,"repo_root",None)
    pp = str(p).replace("\\","/")
    is_abs = pp.startswith("/") or (len(pp)>=2 and pp[1]==":")
    if is_abs:
        if not root:
            return _UNVERIFIED_PREFIX + p        # absolute, unattestable -> fail closed
        cid = _M.canon_cid(pp, root)
        return cid if cid is not None else (_UNVERIFIED_PREFIX + p)
    else:
        # already-relative: canon with empty-safe path; reject .. escapes
        cid = _M.canon_cid(pp, root) if root else _M.canon_cid(pp, "__noroot__")
        # canon_cid on a relative path ignores root for structure; but with a bogus
        # root it still processes the relative branch. Simpler: resolve dots directly.
        segs=[s for s in pp.split("/") if s not in ("",".")]
        out=[]; esc=False
        for s in segs:
            if s=="..":
                if out: out.pop()
                else: esc=True
            else: out.append(s)
        if esc: return _UNVERIFIED_PREFIX + p
        return "/".join(out)


def set_attested_repo_root(root):
    """Back-comat shim: sets the active config root. Prefer `with attested_root(...)`.
    Kept so existing call sites work; internally uses the explicit config object."""
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG=ReplayConfig(repo_root=root)

def norm(path: str, repo_root=...) -> str:
    """Repo-relative identity key. Preserves package structure so
    converters/weight.py and utils/weight.py are DISTINCT nodes. Case handling
    follows the declared filesystem policy (set_case_sensitive)."""
    if not path:
        return ""
    p = path.replace("\\", "/").rstrip("/")
    rr = None if repo_root is ... else repo_root
    return _fold(_repo_relative_raw(p, rr))

def basename(key: str) -> str:
    """The bare filename from a repo-relative key (for leaf-match fallback)."""
    return (key or "").split("/")[-1]

# --- 1. imports from read-file content ---------------------------------------

_READ_CONTENT_RE = re.compile(r"<content>\n(.*?)\n\(End of file", re.DOTALL)
_LINENUM_RE = re.compile(r"^\s*\d+:\s?", re.MULTILINE)
_PATH_RE = re.compile(r"<path>(.*?)</path>")

def parse_read_output(output: str) -> Tuple[Optional[str], Optional[str]]:
    """From a read tool's output, return (normalized_path, clean_source_text)."""
    if not output:
        return None, None
    pm = _PATH_RE.search(output)
    path = norm(pm.group(1)) if pm else None
    cm = _READ_CONTENT_RE.search(output)
    if cm:
        body = _LINENUM_RE.sub("", cm.group(1))  # strip "N: " line prefixes
    else:
        body = None
    return path, body

def imports_of(source: str) -> List[str]:
    """Module names imported by this source, via AST (robust). Returns bare
    top-level module tokens, normalized as basenames (e.g. 'shapes' -> 'shapes')."""
    out = []
    if not source:
        return out
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out.append(_fold(n.name))           # full dotted path (case per policy)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(_fold(node.module))      # full dotted path (case per policy)
    return out

# --- 2. filenames from pytest / error output (noise-filtered) ----------------

_FILE_IN_OUTPUT_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.py)")
_NOISE_HINTS = ("site-packages", "site_packages", "/lib/", "\\lib\\",
                "python3", "python31", "dist-packages", "_pytest", "pluggy",
                "importlib", "<frozen", "/usr/", "appdata")

def filenames_in_output(output: str) -> List[str]:
    """Repo-ish .py filenames named in tool output (pytest tracebacks etc.),
    filtering framework/stdlib/site-packages NOISE (GPT's noisy-traceback case)."""
    if not output:
        return []
    found = []
    for m in _FILE_IN_OUTPUT_RE.finditer(output):
        raw = m.group(1)
        low = raw.replace("\\", "/").lower()
        if any(h in low for h in _NOISE_HINTS):
            continue
        found.append(norm(raw))
    # dedupe, preserve order
    seen = set(); out = []
    for f in found:
        if f and f not in seen:
            seen.add(f); out.append(f)
    return out

# --- 3. filenames in the goal text -------------------------------------------

_GOAL_FILE_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.\w+)")

def filenames_in_goal(goal: str) -> List[str]:
    return [norm(m.group(1)) for m in _GOAL_FILE_RE.finditer(goal or "")]
