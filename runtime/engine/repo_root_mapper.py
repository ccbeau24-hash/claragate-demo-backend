"""
Host-attested repo-root canonicalization (frozen wrapper invariant).
The worker does NOT determine the namespace its authority is evaluated against.

INVARIANT (final): canonicalize against the EXACT host-attested repository root.
Dot-segments (. and ..) are RESOLVED/REJECTED BEFORE containment is decided, so an
escaping path can never masquerade as inside-root. No basename/substring/segment
search. Repo root is passed EXPLICITLY (no process-global state).

Returns:
  a repo-relative cid str  -> the artifact, safely inside the attested root
  "."                      -> the repo root directory itself (ONE representation for
                              every repo-root form: absolute-root, ".", "", "./").
                              A KNOWN identity, distinct from None. Never "".
  None                     -> outside root, escaping (..), or unattributable
                              (caller MUST treat None as UNVERIFIED: no lease)
"""

def _to_segs(p):
    return [s for s in str(p or "").replace("\\", "/").split("/") if s not in ("", ".")]

def _resolve_dots(segs):
    """Resolve '.' (already dropped) and '..'. Returns (resolved_segs, escaped_bool).
    escaped=True if any '..' would pop above the start (an escape)."""
    out=[]; escaped=False
    for s in segs:
        if s == "..":
            if out: out.pop()
            else: escaped=True   # '..' at/above root boundary -> escape
        else:
            out.append(s)
    return out, escaped

def _is_absolute(p):
    p = str(p or "").replace("\\","/")
    return p.startswith("/") or (len(p) >= 2 and p[1] == ":")

def canon_cid(raw_path, repo_root):
    if raw_path is None: return None
    raw = str(raw_path).replace("\\", "/")
    root = str(repo_root or "").replace("\\", "/")
    if not root:
        return None   # no attested root -> UNVERIFIED (caller: no lease)
    # UNC guard (v1 scope): reject UNC roots/paths rather than mangle them.
    if raw.startswith("//") or root.startswith("//"):
        return None

    root_segs, root_esc = _resolve_dots(_to_segs(root))
    if root_esc: return None   # malformed root

    if _is_absolute(raw):
        raw_segs, esc = _resolve_dots(_to_segs(raw))
        if esc:
            return None   # unresolved escape in absolute telemetry -> reject (v1 safe)
        # windows case-insensitive compare on the ROOT prefix only
        def ck(s): return s.casefold()
        if len(raw_segs) < len(root_segs):
            return None
        for a, b in zip(raw_segs[:len(root_segs)], root_segs):
            if ck(a) != ck(b):
                return None   # not under the exact attested root
        rel = raw_segs[len(root_segs):]
        return "/".join(rel) if rel else "."   # "." == attested repo root (NOT "")
    else:
        # already-relative: resolve dots; ANY escape -> reject (cannot prove inside root).
        # An empty relative path (".", "", "./") IS the repo root -> "." (consistent
        # with the absolute-root case above; NEVER "").
        segs, esc = _resolve_dots(_to_segs(raw))
        if esc: return None
        return "/".join(segs) if segs else "."

def canon_inventory(cids, repo_root):
    out=set()
    for c in cids:
        cid=canon_cid(c, repo_root)
        if cid: out.add(cid)
    return out
