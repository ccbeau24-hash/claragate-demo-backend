"""
OPENCODE -> CLARAGATE ADAPTER  (Adapter v0.9 — + evaluation-surface mutation)
Converts a real `opencode export` JSON into a ClaraGate trace, and classifies
test-file edits as addition/deletion/weakening/modification using the
oldString/newString already present in the export. Emits `eval_surface`.
"""
from __future__ import annotations
import json, sys, re as _re
from typing import Any, Dict, List
try:
    import anchor as _anchor
except Exception:
    _anchor=None

_TEST_PATH_HINTS = ("test_", "_test", "/tests/", "conftest")

def _is_test_path(path: str) -> bool:
    p = (path or "").lower().replace("\\", "/")
    base = p.split("/")[-1]
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in p or base == "conftest.py"

def _count_asserts(s: str) -> int:
    return len(_re.findall(r'\bassert\b', s or ""))

def _count_tests(s: str) -> int:
    return len(_re.findall(r'\bdef\s+test_', s or ""))

def _has_skip(s: str) -> bool:
    s = s or ""
    return ("mark.skip" in s) or ("mark.xfail" in s) or ("unittest.skip" in s)

def classify_eval_surface(state):
    """addition / deletion / weakening / modification / neutral, or None if the
    edit does not touch the evaluation surface (a test file)."""
    inp = state.get("input") or {}
    path = inp.get("filePath") or inp.get("path") or ""
    if not _is_test_path(path):
        return None
    old = inp.get("oldString", ""); new = inp.get("newString", "")
    old_a, new_a = _count_asserts(old), _count_asserts(new)
    old_t, new_t = _count_tests(old), _count_tests(new)
    if _has_skip(new) and not _has_skip(old):
        return "weakening"
    if new_t < old_t or new_a < old_a:
        return "deletion"
    if new_t > old_t or new_a > old_a:
        return "addition"
    if old_a == new_a and old_a > 0 and old != new:
        return "modification"
    return "neutral"

def _edit_hash(s):
    """Stable short hash of edit content (for no-op / oscillation detection).
    Content itself is NOT stored — only its hash, so this adds no content exposure."""
    import hashlib
    if s is None: return None
    return hashlib.sha256(s.encode("utf-8","replace")).hexdigest()[:16]

def _has_shell_separator_depth0(cmd):
    """True iff a shell separator (; | && ||) occurs OUTSIDE any quoted region.
    Bounded quote-aware scan for the observed forms (single/double quotes), NOT a
    full shell parser. A ';' inside python -c "a; b" is NOT a shell separator; a ';'
    in `python -c "..." ; echo done` IS. Redirections like 2>&1 are not separators."""
    s = str(cmd or ""); i = 0; n = len(s); q = None
    while i < n:
        ch = s[i]
        if q:                          # inside a quote -> only the matching quote closes it
            if ch == q: q = None
            i += 1; continue
        if ch in ("'", '"'):           # enter a quoted region
            q = ch; i += 1; continue
        # depth-zero shell metacharacters:
        if ch == ';': return True
        if ch == '|':                  # | or ||  (but not part of 2>&1 style)
            return True
        if ch == '&' and i+1 < n and s[i+1] == '&': return True
        i += 1
    return False

def _fingerprint(tool, state):
    inp = state.get("input") or {}
    # WEB TOOLS: identity keys on the URL/query TARGET, not the bare tool name.
    # Distinct URLs / distinct queries are DISTINCT actions (else 7 different fetches
    # collapse to one "webfetch" and manufacture a phantom tool_loop -- the same
    # identity-collapse disease as distinct files collapsing to one cid).
    if tool in ("webfetch","websearch"):
        import hashlib as _h
        target = str(inp.get("url") or inp.get("query") or "").strip().lower()
        if target:
            dig = _h.sha256(target.encode("utf-8","replace")).hexdigest()[:10]
            return f"{tool}:{dig}"
        return tool
    raw = (inp.get("command") or inp.get("filePath") or inp.get("path") or inp.get("pattern") or "")
    raw = str(raw).strip().replace("\\", "/")
    if not raw: return tool
    import re as _re
    low = raw.lower()
    import hashlib as _hh
    def _dig(s): return _hh.sha256(str(s).strip().encode("utf-8","replace")).hexdigest()[:10]
    # EXPLICIT PYTHON GRAMMAR (identity-monotonic: most-specific target, never basename).
    # A compound command (contains ; && || or a pipe) is NOT a single python op ->
    # skip the python-specialization and fall through to head/compound handling.
    _compound = _has_shell_separator_depth0(raw)
    if not _compound:
        # python - <<EOF / python -   -> stdin/heredoc (a distinct op, may fail)
        if _re.match(r"(?:[^\s]*/)?python[0-9.]*\s+-\s*(?:<<|$)", low) or _re.search(r"python[0-9.]*\s+-\s*<<", low):
            return f"{tool}:python_stdin"
        # python -m <module>
        m = _re.match(r"(?:[^\s]*/)?python[0-9.]*\s+-m\s+([A-Za-z0-9_.]+)", low)
        if m: return f"{tool}:python_module:{m.group(1)[:24]}"
        # python -c "code"  -> digest of the code (distinct code = distinct op)
        m = _re.search(r"python[0-9.]*\s+-c\s+(.+)$", raw, _re.S)
        if m: return f"{tool}:python_code:{_dig(m.group(1))}"
        # python <script>  (quoted or unquoted, incl. drive-colon Windows paths)
        # -> digest of the FULL normalized target (NOT basename -> no a/utils vs b/utils collapse)
        m = _re.search(r'python[0-9.]*\s+("[^"]+\.py"|\'[^\']+\.py\'|[^\s;&|]+\.py)', raw)
        if m:
            tgt=m.group(1).strip('\'"').replace("\\","/")
            return f"{tool}:python_script:{_dig(tgt)}"
    # 3) the command HEAD (first bare token, minus a leading path) + known git subcmd
    head_tok = _re.split(r"[\s;&|]+", low)[0]
    head = head_tok.split("/")[-1]
    if head in ("git",):
        # QUOTE-AWARE tokenizer: keep quoted regions (incl. paths with spaces) as ONE
        # token, so `git -C "C:/path with space/repo" status` finds `status`, not `with`.
        def _shell_tokens(s):
            toks=[]; cur=""; q=None
            for ch in s.strip():
                if q:
                    if ch==q: q=None
                    else: cur+=ch
                elif ch in ("'",'"'): q=ch
                elif ch.isspace():
                    if cur: toks.append(cur); cur=""
                else: cur+=ch
            if cur: toks.append(cur)
            return toks
        toks=_shell_tokens(raw)
        # locate the git token (may be a path .../git)
        j=1
        if toks and toks[0].split("/")[-1].lower()!="git":
            for k,t in enumerate(toks):
                if t.split("/")[-1].lower()=="git": j=k+1; break
        _val_opts={"-C","-c","--git-dir","--work-tree","--namespace","--exec-path","--super-prefix"}
        sub=None
        while j < len(toks):
            t=toks[j]
            if t in _val_opts: j+=2; continue           # option + its value
            if t.startswith("--") and "=" in t: j+=1; continue   # --opt=val
            if t.startswith("-"): j+=1; continue        # bare flag
            sub=t; break                                # first non-option token = subcommand
        if sub:
            sub=_re.sub(r"[^a-z0-9_-]","",sub.lower())[:20]
            # IDENTITY: git_<subcmd>:<digest(full normalized invocation)> for ALL git
            # commands. Distinct arguments (git log --grep=foo vs --grep=bar) -> DISTINCT
            # identities; the SAME exact invocation repeated -> SAME identity (recurrence
            # eligible). Errs toward MISSING an equivalent recurrence rather than
            # MANUFACTURING a phantom hard-stop from distinct actions (safe direction).
            return f"{tool}:git_{sub}:{_dig(raw)}"
        return f"{tool}:git"
    if head in ("pytest",): return f"{tool}:pytest"
    if head in ("ls","dir","cat","type","tree","find","grep","npm","make","cargo","go",
                "get-childitem","get-content","gci","gc","mypy","flake8","pycodestyle",
                "pip","node","yarn","tox","ruff","black","pylint"):
        return f"{tool}:{head}"
    # fallback: the head token (bounded), NEVER an argument
    return f"{tool}:{head[:24]}" if head else tool
def _is_test_command(state):
    cmd = str((state.get("input") or {}).get("command") or "").lower()
    return "pytest" in cmd or "test" in cmd

def _is_web_tool_state(state):
    # a web tool is identified by url/query input (adapter passes tool via convert;
    # here we detect by input shape since _errored only gets state).
    inp = state.get("input") or {}
    return ("url" in inp) or ("query" in inp)

def _errored(state):
    # W1: a WEB retrieval failure (HTTP 404 etc.) is NOT a code-EXECUTION error.
    # It is a distinct failure kind (retrieval/network), classified separately so it
    # does NOT compound as execution_error. (Answer-seeking policy P1 is untouched.)
    if _is_web_tool_state(state):
        return False   # web retrieval failure is not an execution_error
    if _is_test_command(state): return False
    if str(state.get("status","")).lower() in ("error","failed"): return True
    ec = (state.get("metadata") or {}).get("exit")
    return isinstance(ec,int) and ec!=0

def _exit_is_attributable(cmd):
    """The metadata exit code is attributable to the TEST RUNNER only when the
    command is a DIRECT runner invocation, not a pipeline/compound where the
    reported exit belongs to the OUTER shell (a filter, a chained command, etc.).
    Pipeline/compound -> exit provenance AMBIGUOUS."""
    if not cmd: return False
    import re as _re
    c = str(cmd)
    # compound/pipeline separators whose outer exit may not be the runner's:
    #   |  (pipe)   ;  &&  ||  (chaining)   and PowerShell filters after a pipe.
    # NOTE: `2>&1` is redirection, NOT a pipe -> still attributable.
    stripped = _re.sub(r"2>&1", "", c)   # ignore stderr redirection
    if "|" in stripped: return False     # piped into another command
    if _re.search(r"(?:;|&&|\|\|)", stripped): return False   # chained
    return True

def _test_failed(state):
    if not _is_test_command(state): return False
    ec = (state.get("metadata") or {}).get("exit")
    if not isinstance(ec,int): return False
    cmd = (state.get("input") or {}).get("command","")
    if not _exit_is_attributable(cmd):
        # AMBIGUOUS exit provenance (pipeline/compound): do NOT confidently mark red.
        # Observer uncertainty constrains governor certainty -> indeterminate verifier.
        return False
    return ec!=0

def _snippet(state):
    meta = state.get("metadata") or {}
    out = state.get("output") or meta.get("output") or ""
    return str(out).replace("\r"," ").replace("\n"," ")[:80]

def _full_output(state):
    """Full tool output with newlines preserved (for provenance parsing of file
    content / tracebacks). Capped generously to bound size."""
    meta = state.get("metadata") or {}
    out = state.get("output") or meta.get("output") or ""
    return str(out)[:20000]

def _existed_before(state, tool):
    """Did the file exist BEFORE this operation? true/false/None(unknown).
    An EDIT with a non-empty oldString proves prior content -> existed.
    A WRITE (full content, no oldString) is AMBIGUOUS -> unknown.
    Non-file tools -> None."""
    if tool not in ("edit","write"): return None
    inp = state.get("input") or {}
    if tool=="edit":
        old = inp.get("oldString")
        if old is not None and str(old).strip()!="":
            return True   # had content to replace -> existed
        # empty oldString edit is ambiguous
        return None
    # write tool: ambiguous whether creating or overwriting
    return None

def _artifact_path(state):
    """Repo-relative identity of the file this tool acted on, from structured
    input (filePath/path). None for non-file tools."""
    import provenance as _P
    inp = state.get("input") or {}
    fp = inp.get("filePath") or inp.get("path")
    return _P.norm(fp) if fp else None

def _make_anchor(state, tool):
    """External test anchor for a bash step whose command ran tests. Returns a
    plain dict (adapter stays serializable) or None."""
    if tool!="bash" or _anchor is None:
        return None
    cmd = str((state.get("input") or {}).get("command") or "").lower()
    # Require a real test-RUNNER invocation, not just the substring "test"
    # (which matches "latest", "test_data.json", "contest", etc).
    import re as _re
    is_runner = bool(_re.search(r"\b(pytest|py\.test|unittest|nose2|tox|go test|cargo test|npm test|jest|vitest|mocha)\b", cmd)) \
                or bool(_re.search(r"python\s+-m\s+pytest", cmd))
    if not is_runner:
        return None
    exit_code = (state.get("metadata") or {}).get("exit")
    _cmd = (state.get("input") or {}).get("command","")
    # AMBIGUOUS exit provenance (pipeline/compound) -> pass exit_code=None so the
    # anchor decides green/red from the runner's OWN output text, not the outer shell.
    _attrib_exit = exit_code if (isinstance(exit_code,int) and _exit_is_attributable(_cmd)) else None
    out = state.get("output") or (state.get("metadata") or {}).get("output") or ""
    a = _anchor.extract_test_anchor(_attrib_exit, str(out))
    return {"ran":a.ran, "green":a.green, "exit_code":a.exit_code,
            "collected":a.collected, "passed":a.passed, "failed":a.failed,
            "errors":a.errors, "forgery_suspected":a.forgery_suspected}

def convert(session, run_id, goal):
    messages = session.get("messages", [])
    steps=[]; prev_total=0; idx=0
    for msg in messages:
        info = msg.get("info", {})
        if info.get("role")!="assistant": continue
        total = ((info.get("tokens") or {}).get("total")) or prev_total
        per_cost = max(0, total-prev_total); prev_total=total
        tps=[p for p in msg.get("parts",[]) if p.get("type")=="tool"]
        per = (per_cost//len(tps)) if tps else 0
        for p in tps:
            state=p.get("state") or {}; tool=p.get("tool","unknown")
            steps.append({
                "index":idx, "action":_fingerprint(tool,state), "tool":tool,
                "retries":0, "tokens":per, "output_snippet":_snippet(state),
                "full_output":_full_output(state),
                "error":_errored(state), "test_failed":_test_failed(state),
                "eval_surface": classify_eval_surface(state) if tool=="edit" else None,
                "test_anchor": _make_anchor(state, tool),
                "artifact_path": _artifact_path(state),
                "existed_before": _existed_before(state, tool),
                "bash_command": (state.get("input",{}) or {}).get("command") if tool=="bash" else None,
                "edit_old_h": _edit_hash((state.get("input",{}) or {}).get("oldString")) if tool in ("edit","write") else None,
                "edit_new_h": _edit_hash((state.get("input",{}) or {}).get("newString")) if tool in ("edit","write") else None,
                "on_goal":None,
            })
            idx+=1
    return {"run_id":run_id,"goal":goal,
            "session_id":session.get("info",{}).get("id",""),
            "model":(session.get("info",{}).get("model") or {}).get("id",""),
            "total_tokens":prev_total,"steps":steps}

if __name__=="__main__":
    if len(sys.argv)<2:
        print("Usage: python3 opencode_adapter.py <export.json> [run_id] [\"goal\"]", file=sys.stderr); sys.exit(1)
    path=sys.argv[1]; run_id=sys.argv[2] if len(sys.argv)>2 else "opencode_run"
    goal=sys.argv[3] if len(sys.argv)>3 else "OpenCode coding session"
    with open(path, encoding="utf-8") as f: session=json.load(f)
    trace=convert(session,run_id,goal)
    if not trace["steps"]: print("WARNING: no tool steps found.", file=sys.stderr)
    print(json.dumps(trace, indent=2))
