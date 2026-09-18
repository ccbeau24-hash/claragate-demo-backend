"""H1 — deterministic eligible-goal-identifier extraction (v1 grammar, PINNED).
ROOT-ANCHORED + POSITIVE-CLOSED. Authority-bearing modification syntax is valid
ONLY as a TOP-LEVEL IMPERATIVE (optionally after 'please'), not a phrase found
anywhere inside arbitrary text. This closes the embedded-phrase class by
construction: "Should I fix parser.py?" / "Search for 'fix parser.py'" cannot
match because the imperative is not at the task root. No context blacklist needed.
Source = initial/top-level user task ONLY.
"""
import re

_VERBS = ["fix","update","modify","edit","refactor","rename","change","remove","delete"]
_VERB_RE = r"(?:" + "|".join(_VERBS) + r")"
# TARGET: a call-form foo() OR a python filename ...foo.py. The token MUST
# TERMINATE cleanly — not be a prefix of a longer token (parser.pyc, slugify()foo,
# parser.py/evil). Enforced by a trailing negative lookahead.
_TARGET = r"([A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)|[A-Za-z0-9_./\\-]+\.py)(?![A-Za-z0-9_./\\])"
_NEG = r"(?:do\s*n['o]?t|never)\s+"
# a small allowed polite/opening prefix at the ROOT, then the imperative.
_ROOT = r"^\s*(?:please\s+)?"

def _canon_stem(tok):
    tok=tok.strip(); tok=re.sub(r"\s*\(\s*\)$","",tok).strip()
    tok=tok.replace("\\","/").split("/")[-1].strip()
    return tok[:-3] if tok.endswith(".py") else tok

def _canon_path(tok):
    """canonical path form: forward slashes, strip a leading ./"""
    p=tok.strip().replace("\\","/")
    return p[2:] if p.startswith("./") else p

def _is_explicit_path(tok):
    """the user gave a PATH (contains a separator) rather than a bare stem/call."""
    t=tok.strip()
    if t.endswith("()"): return False          # call form -> bare
    tp=t.replace("\\","/")
    return "/" in tp                            # any separator -> explicit path

def _target_of(raw):
    """classify a matched target into ('path', canonical_path) or ('stem', stem)."""
    raw=raw.strip()
    if _is_explicit_path(raw):
        return ("path", _canon_path(raw))
    return ("stem", _canon_stem(raw))

# the two positive constructions, matched ONLY at the task root:
_DIRECT = _VERB_RE + r"\s+(?:the\s+)?" + _TARGET
_K1TEMPLATE = (r"add\s+(?:a\s+|an\s+|the\s+)?[A-Za-z_][A-Za-z0-9_]*\s+"
               r"(?:option|parameter|argument|flag|method|function|field|attribute)\s+"
               r"to\s+(?:the\s+)?" + _TARGET)

def positive_targets(goal):
    """Root-anchored structured targets: ('path', canonical_path) preserves an
    explicit user path; ('stem', stem) for a bare/call target. Empty if none.
    This preserves the specificity the user gave (more-specific identity must not
    become less-specific authority)."""
    g = goal or ""
    if re.match(_ROOT + _NEG, g, re.I):
        return set()
    for pat in (_DIRECT, _K1TEMPLATE):
        m = re.match(_ROOT + pat, g, re.I)
        if m:
            return {_target_of(m.group(1))}
    return set()

def positive_tokens(goal):
    """Root-anchored: the task MUST BEGIN (after optional 'please') with a positive
    imperative. A modify phrase embedded anywhere else yields NOTHING."""
    g = goal or ""
    # a negated root imperative is not positive (handled here so "do not fix x" at
    # root doesn't match the positive branch).
    if re.match(_ROOT + _NEG, g, re.I):
        return set()
    for pat in (_DIRECT, _K1TEMPLATE):
        m = re.match(_ROOT + pat, g, re.I)   # ANCHORED at start, not finditer
        if m:
            return {_canon_stem(m.group(1))}
    return set()

def prohibited_targets(goal):
    """Explicit prohibition, aligned to the same verb set + the K1 add-template.
    Prohibition may appear anywhere (a prohibition anywhere should block)."""
    g = goal or ""
    out=set()
    neg_verbs = "|".join(_VERBS + ["touch","add"])
    for m in re.finditer(_NEG + r"(?:" + neg_verbs + r")\s+(?:the\s+)?" + _TARGET, g, re.I):
        out.add(_canon_stem(m.group(1)))
    for m in re.finditer(_NEG + _K1TEMPLATE, g, re.I):
        out.add(_canon_stem(m.group(1)))
    return out
