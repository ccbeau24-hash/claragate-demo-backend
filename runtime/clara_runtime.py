#!/usr/bin/env python3
"""Clara Runtime v0.1 — invariant-correct, four trust boundaries enforced & falsifier-proven.
Shell never issues CLEARED/NOT_CLEARED. It: (a) verifies sealed engine provenance against a PINNED
anchor + pins the product harness hash; (b) validates command<->identity; (c) establishes DECISIVE
verifier standing (the last designated-identity event at/before closure IS the host-attested
invocation, structurally parsed); (d) computes completion via the sealed primitive at the CORRECT
closure boundary, with replay-equivalence. vNext15 alone adjudicates GREEN/RED. Doors: 0/2/3.
Invalid standing -> UNVERIFIED (never a manufactured NOT_CLEARED)."""
import os, sys, json, hashlib, re, shlex, argparse, traceback

# ---- PINNED roots of trust (NOT caller-substitutable) ----
INSTALLER_ANCHOR   = "763e9e8bd9b21e63e4db93846d429ff49a6cdfb12f2de6faf3cea9ce2bfc93ea"
PRODUCT_HARNESS    = "claragate_stage1.py"
PRODUCT_HARNESS_SHA= "d8c0fc2ba1a23847135c2d634a661d8571d9356c7ccbb49bd1957502d4f64cff"

DOOR={"CLEARED":0,"NOT_CLEARED":2,"UNVERIFIED":3,"CONFIG_ERROR":3,"RUNTIME_ERROR":3}
def _sha(p): return hashlib.sha256(open(p,"rb").read()).hexdigest()

def verify_engine(engine_dir, installer_path):
    if not engine_dir or not os.path.isdir(engine_dir): return False,"engine dir absent"
    if not installer_path or not os.path.exists(installer_path): return False,"installer absent"
    if _sha(installer_path)!=INSTALLER_ANCHOR: return False,"installer hash != PINNED anchor"
    m=re.search(r"ALL_HASHES=(\{.*?\})\nGUARD_COUNT", open(installer_path).read(), re.S)
    if not m: return False,"manifest not found"
    import ast; man=ast.literal_eval(m.group(1))
    for fn in ["completion_plane_v1.py","opencode_adapter.py","observe_authority.py","authority_scoring.py",
               "lease_v1.py","eval_surface.py","h1_extract.py","clara_observer_v09.py","authority.py",
               "recovery.py","provenance.py","repo_root_mapper.py","buffer_invariants.py"]:
        p=os.path.join(engine_dir,fn)
        if fn not in man: 
            if not os.path.exists(p): return False,f"engine missing {fn}"
            continue
        if not os.path.exists(p) or _sha(p)!=man[fn]: return False,f"engine module tampered/missing: {fn}"
    # anchor.py: in ALL_HASHES; hash-verify it (it determines whether evidence is authoritative)
    ap=os.path.join(engine_dir,"anchor.py")
    if not os.path.exists(ap): return False,"engine missing anchor.py"
    if "anchor.py" in man and _sha(ap)!=man["anchor.py"]: return False,"anchor.py tampered (hash != manifest)"
    # PRODUCT HARNESS: outside historical manifest -> pin its approved product hash
    hp=os.path.join(engine_dir,PRODUCT_HARNESS)
    if not os.path.exists(hp): return False,f"product harness absent: {PRODUCT_HARNESS}"
    if _sha(hp)!=PRODUCT_HARNESS_SHA: return False,f"product harness hash != pinned: {PRODUCT_HARNESS}"
    return True,"engine + product harness verified"

# ---- structured pytest invocation parse (NOT substring); ignore 2>&1; v0.1 rejects compound ----
def parse_pytest_invocation(command):
    """Return (ok, argset) if command is a DIRECT . Compound (&&, ;, |, cd) -> not ok."""
    c=command.strip()
    if re.search(r"(\|\||&&|;|\bcd\b|\|)", c): return False, None      # v0.1: no compound
    # strip trailing redirections like 2>&1 / >log
    c=re.sub(r"\s+\d?>&?\d?\S*", "", c).strip()
    try: toks=shlex.split(c)
    except Exception: return False, None
    # expect: python|python3 -m pytest <args...>
    if len(toks)>=3 and re.match(r"python[0-9.]*$", os.path.basename(toks[0])) and toks[1]=="-m" and toks[2]=="pytest":
        return True, tuple(toks[3:])   # FULL canonical argv (flags INCLUDED); no undeclared options allowed
    return False, None

def _norm_dir(p):
    if not p: return None
    return os.path.normpath(str(p).strip().replace("\\","/").rstrip("/")).lower()

def decisive_verifier_has_standing(session, contract_cmd, verifier_id, engine_dir, repo_root):
    """The LAST designated-identity event at/before closure must (a) canonically EQUAL the host-attested
    invocation (no undeclared flags) AND (b) carry a sealed VERIFIED test_anchor (ran==true). The
    verified-ness comes from the SEALED adapter, not a runtime parser. Runtime never reads GREEN/RED."""
    sys.path.insert(0, engine_dir)
    import opencode_adapter as AD
    from clara_observer_v09 import Trace
    req_ok, req_argv = parse_pytest_invocation(contract_cmd)
    if not req_ok: return False,"contract verifier command is not a direct python -m pytest invocation (v0.1)"
    # build the sealed trace so we read the SEALED anchor on the decisive step
    tr=Trace.from_dict(AD.convert(session, session.get("info",{}).get("id","run"), "goal"))
    cs=closure_step_index(session, engine_dir)
    if cs is None: return False,"no mechanical closure"
    last=None
    for i,s in enumerate(tr.steps):
        if i>cs: break
        if getattr(s,"action","")==verifier_id:
            last=s
    if last is None:
        return False, f"no event with designated verifier identity {verifier_id} at/before closure"
    # (b) the decisive event must carry a sealed VERIFIED anchor
    anc=getattr(last,"test_anchor",None)
    if not anc or not anc.get("ran"):
        return False, "decisive designated verifier produced no authoritative test result (anchor.ran != true)"
    # (a) canonical invocation equivalence (recover the command for this step from the raw session)
    dec_cmd=_command_of_step(session, verifier_id, cs, engine_dir)
    ok,argv=parse_pytest_invocation(dec_cmd or "")
    if not ok: return False, f"decisive verifier event not a direct pytest invocation: {dec_cmd!r}"
    if argv!=req_argv:
        return False, f"decisive verifier argv != host-attested (undeclared flags?): need {req_argv}, got {argv}"
    # (c) execution-location binding: the decisive verifier must have run in the host-attested repo_root
    dec_wd=_workdir_of_step(session, verifier_id, closure_step_index(session, engine_dir), engine_dir)
    if not dec_wd:
        return False, "decisive verifier workdir absent/ambiguous"
    if _norm_dir(dec_wd)!=_norm_dir(repo_root):
        return False, f"decisive verifier ran in wrong location: workdir {dec_wd!r} != host-attested repo_root {repo_root!r}"
    return True,"decisive verifier event has standing (canonical match + verified anchor + correct workdir)"

def _workdir_of_step(session, verifier_id, closure_step, engine_dir):
    sys.path.insert(0, engine_dir); import opencode_adapter as AD
    idx=0; last_wd=None
    for m in session.get("messages",[]):
        info=m.get("info",{}) or {}
        if info.get("role")!="assistant": continue
        for p in [pp for pp in m.get("parts",[]) if pp.get("type")=="tool"]:
            if idx<=closure_step:
                inp=(p.get("state",{}) or {}).get("input",{}) or {}
                cmd=inp.get("command","")
                if AD._fingerprint("bash",{"input":{"command":cmd}})==verifier_id:
                    last_wd=(inp.get("workdir") or inp.get("cwd")
                             or (info.get("path") or {}).get("cwd"))
            idx+=1
        if info.get("finish")=="stop": break
    return last_wd

def _command_of_step(session, verifier_id, closure_step, engine_dir):
    """Recover the raw command of the LAST designated-identity bash step at/before closure."""
    sys.path.insert(0, engine_dir); import opencode_adapter as AD
    idx=0; last=None
    for m in session.get("messages",[]):
        info=m.get("info",{}) or {}
        if info.get("role")!="assistant": continue
        for p in [pp for pp in m.get("parts",[]) if pp.get("type")=="tool"]:
            if idx<=closure_step:
                cmd=((p.get("state",{}) or {}).get("input",{}) or {}).get("command","")
                if AD._fingerprint("bash",{"input":{"command":cmd}})==verifier_id: last=cmd
            idx+=1
        if info.get("finish")=="stop": break
    return last

def closure_step_index(session, engine_dir):
    """Trace-step index of the adjudicated closure (last tool step of the FIRST finish==stop msg)."""
    idx=0; step_idx=-1; closure=None
    for m in session.get("messages",[]):
        info=m.get("info",{}) or {}
        if info.get("role")!="assistant": continue
        for _ in [p for p in m.get("parts",[]) if p.get("type")=="tool"]:
            step_idx=idx; idx+=1
        if info.get("finish")=="stop": closure=step_idx; break
    return closure

def load_bound_contract(cp, csha, engine_dir):
    if not cp or not os.path.exists(cp): return None,"contract absent"
    if _sha(cp)!=csha: return None,"contract hash != host-attested (tampered)"
    try: c=json.load(open(cp))
    except Exception as e: return None,f"contract parse: {e}"
    v=c.get("completion",{}).get("verifier",{})
    if not v.get("identity") or not v.get("command"): return None,"contract missing verifier identity/command"
    sys.path.insert(0,engine_dir); import opencode_adapter as AD
    fp=AD._fingerprint("bash",{"input":{"command":v["command"]}})
    if fp!=v["identity"]: return None,f"identity {v['identity']} != fingerprint(command) {fp}"
    return c,None

def score(session_path, contract_path, contract_sha, inventory_path, repo_root, goal, engine_dir, installer):
    ok,reason=verify_engine(engine_dir, installer)
    if not ok: return "CONFIG_ERROR", f"engine provenance: {reason}"
    if engine_dir not in sys.path: sys.path.insert(0, engine_dir)
    contract,cerr=load_bound_contract(contract_path, contract_sha, engine_dir)
    if cerr: return "CONFIG_ERROR", cerr
    v=contract["completion"]["verifier"]; verifier_id=v["identity"]; required_cmd=v["command"]
    if contract.get("completion",{}).get("acceptance_required",False):
        at=v.get("acceptance_test") or {}
        if not at.get("path") or not at.get("sha256"): return "CONFIG_ERROR","acceptance_required but not bound"
        # the acceptance path MUST be part of the designated verifier invocation, else contract is inconsistent
        _ok,_argv=parse_pytest_invocation(required_cmd)
        if not _ok or at["path"] not in _argv:
            return "CONFIG_ERROR","acceptance_required but acceptance_test.path not in designated verifier invocation"
        if not os.path.exists(at["path"]): return "UNVERIFIED","required acceptance evidence absent"
        if _sha(at["path"])!=at["sha256"]: return "CONFIG_ERROR","acceptance test hash != attested"
    try:
        if not os.path.exists(session_path): return "RUNTIME_ERROR","session absent"
        d=json.load(open(session_path,encoding="utf-8"))
    except Exception as e: return "RUNTIME_ERROR",f"session load: {e}"
    # DECISIVE-verifier standing (invalid standing -> UNVERIFIED, NOT a manufactured verdict)
    st_ok,st_why=decisive_verifier_has_standing(d, required_cmd, verifier_id, engine_dir, repo_root)
    if not st_ok: return "UNVERIFIED", f"standing: {st_why}"
    # CORRECT closure boundary + sealed completion primitive WITH replay-equivalence
    try:
        import completion_plane_v1 as CP, opencode_adapter as AD
        from clara_observer_v09 import Trace
        cs=closure_step_index(d, engine_dir)
        if cs is None: return "UNVERIFIED","no mechanical closure (finish==stop)"
        def one():
            tr=Trace.from_dict(AD.convert(d, d.get("info",{}).get("id","run"), goal))
            return CP.compute_completion(tr, verifier_id, closure_step=cs, verifier_required=True).clearance
        op=one(); rp=one()
        if op!=rp: return "RUNTIME_ERROR", f"replay-equivalence mismatch op={op} rp={rp}"
        clr=op
    except Exception as e:
        return "RUNTIME_ERROR", f"completion: {e}\n{traceback.format_exc()[-300:]}"
    if clr=="CLEARED": return "CLEARED", f"sealed vNext15 CLEARED (decisive designated verifier GREEN at closure {cs})"
    if clr=="NOT_CLEARED": return "NOT_CLEARED", f"sealed vNext15 NOT_CLEARED (designated verifier RED/OPEN at closure {cs})"
    return "UNVERIFIED", f"no verdict (clearance={clr})"

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    for a in ("--session","--contract","--contract-sha","--inventory","--repo-root","--goal","--engine","--installer"):
        ap.add_argument(a, required=(a!="--goal"))
    n=ap.parse_args()
    try: state,detail=score(n.session,n.contract,n.contract_sha,n.inventory,n.repo_root,n.goal or "task",n.engine,n.installer)
    except Exception as e: state,detail="RUNTIME_ERROR",str(e)
    door=DOOR.get(state,3)
    print(json.dumps({"host_exit":door,"internal_state":state,"detail":detail,
                      "authority":"sealed_vNext15" if state in ("CLEARED","NOT_CLEARED") else "runtime_fallback"},indent=2))
    sys.exit(door)
