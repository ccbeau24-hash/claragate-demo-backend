"""
ClaraGate authority-aware observer (v1, experimental).

Drift is now defined by AUTHORITY + COHERENCE, not timing or agent-authored
grounding:
  - a TRUSTED target (chains to an external anchor)        -> no drift
  - a GROUNDED target (related, but agent-mediated only)   -> weak/watching
  - continued expansion into GROUNDED/UNSUPPORTED files WITHOUT external progress
    (no real red->green movement) -> drift accumulates (C/D incoherence)
  - a strong WANDER command (dir/dump) once grounded       -> drift
  - EXPANSION COUPLED TO REAL PROGRESS (tests moved red->green via a verified
    runner) discharges accumulated drift — legitimate work.

External anchors and the red->green signal come from anchor.py (agent-independent).
Grounding edges come from provenance.py (agent-mediated). All other v0.9 signals
(loop, cost, error, retry, eval-surface) are unchanged.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, List
import provenance as P
import re as _re
import authority as AU
import clara_observer_v09 as V
import authority_scoring as S
import evidence_limits as EL
from authority_policy import build_policy

_WANDER=("get-childitem","get-content","dir","ls","cat","type","tree","find","gci","gc ")
_ONTASK=("pytest","test","python","npm","build","lint","git","make","cargo","go ")

def _t(action): return action.split(":",1)[1].strip().lower() if ":" in action else ""

def canonical_artifact_id(step, fallback_tgt):
    """ONE canonical identity for the artifact a step refers to, used everywhere
    (note_read/note_edit/register/status/drift/WHY). Evidence priority (GPT):
      1. host-returned <path> from a read tool's output (strongest available),
      2. repo-relative edit/write target from structured tool input filePath,
      3. bare action fingerprint (fallback only).
    Display strings (read:temperature.py) stay separate from this identity."""
    import provenance as _P
    # 1) structured repo-relative artifact_path from the tool INPUT filePath —
    #    the SAME field and normalization for reads AND edits/writes. This is the
    #    canonical identity; using it for every tool guarantees one file -> one id.
    ap = getattr(step,"artifact_path",None)
    if ap: return ap
    # 2) fallback for reads without structured input: the host-returned <path>.
    fo = getattr(step,"full_output",None)
    if getattr(step,"tool",None)=="read" and fo:
        parsed,_ = _P.parse_read_output(fo)
        if parsed: return parsed
    # 3) last resort: the bare action fingerprint.
    return fallback_tgt

@dataclass
class Why:
    index:int; action:str; status:str; anchor:str; progress:str
    signals:Dict[str,float]; decision:str; reason:str

def observe_authority(trace, want_why=False, diagnostic=False, host_inventory=None):
    # DETERMINISM BY CONSTRUCTION: policy is BUILT from the immutable source and
    # PASSED through scoring. Nothing reads a process-global for policy.
    policies = build_policy()
    _evidence_limits=[]   # structured epistemic-limit records (omitted when empty)
    S.assert_policy_provenance(policies)     # positive proof: policy is the param
    buf=S.NoticeBuffer(); recs=[]; whys=[]
    model=AU.AuthorityModel(); model.seed_goal(trace.goal)
    past_costs=[]; recent=[]; open_errors={}
    prev_failed=None; disconnected=0; grounded_started=False
    orient_reads=0   # consecutive in-repo orientation reads without progress (bounded)
    _disconnected_writes=[]
    _last_read_imports={}    # cid -> import set at last read
    _edit_state_hist={}      # cid -> set of prior SUCCESSFUL new-content hashes
    _last_success_nh={}      # cid -> the LAST successful new-content hash (for reversion)
    _verifier_failing=False  # last relevant verifier outcome was RED (trajectory state)
    _last_read_content={}    # cid -> content fingerprint at last read (full-content delta)
    _edit_since_read=set()   # cids edited via edit-tool since last read
    _bash_named_targets={}   # basename -> set of path-hints from bash commands (ambiguity-aware)
    _bash_mutated_unread=set() # cids a bash cmd mutated that were NOT reread (C2)
    _unobservable_mutation=False  # a mutating bash command with unresolvable target ran
    diag={'steps':0,'anchors_seen':0,'anchors_ran':0,'reads':0,'imports_resolved':0,'files_trusted_via_chain':0,'forgeries':0,'regressions':0}

    for i,step in enumerate(trace.steps):
        act=step.action; tool=step.tool; tgt=_t(act); sig={}
        cid=canonical_artifact_id(step, tgt)   # ONE canonical artifact identity
        _known_before_step=set(model.known_files)   # snapshot for CREATE-vs-MODIFY
        anchor=getattr(step,"test_anchor",None)
        progress="-"
        diag['steps']+=1
        if anchor: diag['anchors_seen']+=1
        if anchor and anchor.get('ran'): diag['anchors_ran']+=1
        if anchor and anchor.get('forgery_suspected'): diag['forgeries']+=1

        # ---- bash filesystem-mutation OBSERVABILITY (epistemic boundary) ----
        if tool=="bash":
            _cmd=getattr(step,"bash_command",None) or ""   # FULL command string
            _looks_mutating = any(h in _cmd.lower() for h in ("add-content","set-content","sed","tee"," cp "," mv ","echo","dd","xargs",">>",">"))
            if _looks_mutating:
                # can we resolve WHICH file? parse path-like tokens from the command.
                # A pipe/glob/xargs command has no single resolvable target.
                _ambiguous = ("|" in _cmd) or ("xargs" in _cmd.lower()) or ("*" in _cmd)
                _targs=[t for t in _re.findall(r"[\w./\\-]+\.\w+", _cmd)]
                if _targs and not _ambiguous:
                    for _tf in _targs:
                        _bn=P.basename(P.norm(_tf))
                        _bash_named_targets.setdefault(_bn,set()).add(P.norm(_tf))
                        _bash_mutated_unread.add(P.norm(_tf))   # C2: pending unread mutation
                else:
                    _unobservable_mutation=True   # mutating command, unresolvable target (probe D)

        # ---- external anchor intake (agent-independent) ----
        if tool=="bash" and anchor and anchor.get("ran") and _bash_mutated_unread:
            # C2: a verifier ran while a bash-mutated file was never reread. The
            # offline trace cannot causally attribute this result to the mutation.
            for _mf in sorted(_bash_mutated_unread):
                _evidence_limits.append(EL.make("UNVERIFIABLE_EVAL_DEPENDENCY",
                    "a mutation-capable shell command TARGETED this artifact and it was not re-observed before a later verifier (pytest) executed; the offline trace cannot establish whether the file's bytes actually changed, nor whether any such change affected the verifier result",
                    "COMMAND_DERIVED", artifact=_mf, step_index=i))
            _bash_mutated_unread.clear()
        if tool=="bash" and anchor and anchor.get("ran"):  # anchor.ran already requires a real exit code
            # files the REAL runner named become externally trusted
            named=P.filenames_in_output(getattr(step,"full_output",None) or "")
            model.note_real_test(anchor, named)
            # coherence: did the failing set shrink? (real progress)
            failed=anchor.get("failed")
            if prev_failed is not None and failed is not None:
                if failed<prev_failed:
                    progress="red->green (failing set shrank)"
                    # discharge accumulated drift — legitimate progress
                    for n in buf.notices:
                        if n.signal_type=="interpretive_drift": n.intensity*=0.4
                    disconnected=0
                elif failed>prev_failed:
                    progress="regression (more failing) [telemetry only]"
                    # NOTE: regression is TELEMETRY ONLY this pass — not a governance
                    # signal. Two pytest runs may cover different test scopes, so a
                    # rising failed-count is not proven agent-caused. Record, don't act.
            if anchor.get("green"): progress="green (verified pass)"
            prev_failed=failed if failed is not None else prev_failed

        # ---- grounding intake (agent-mediated) ----
        if tool=="read":
            diag['reads']+=1
            _,body=P.parse_read_output(getattr(step,"full_output",None) or step.output_snippet or "")
            # CONTENT-CHANGE DETECTION (observable bash mutation): if we've read
            # this file before, its import set changed, and NO edit-tool touched it
            # since, an EXTERNAL (bash) mutation occurred -> treat as worker-modified
            # so newly-appeared imports are agent-influenced (laundering defense).
            cur_imports=set(P.imports_of(body or ""))
            import hashlib as _hl
            cur_content_fp=_hl.sha256((body or "").encode()).hexdigest()
            # TRUNCATION AWARENESS: a read output is COMPLETE only if it carries
            # the end-of-file marker. If truncated, a same-head fingerprint does
            # NOT prove "unchanged" — the tail is unseen. Track completeness so the
            # comparison can degrade to INDETERMINATE rather than false "unchanged".
            _full=getattr(step,"full_output",None) or ""
            cur_complete = ("End of file" in _full) and ("truncated" not in _full.lower())
            # EPISTEMIC (probe B): first read of a file a prior bash command named
            # -> we cannot know if this content is original or bash-mutated.
            _bn=P.basename(cid)
            if cid not in _last_read_content and _bn in _bash_named_targets:
                # AMBIGUITY CHECK: how many KNOWN files share this basename?
                _same_base=[f for f in model.known_files if P.basename(f)==_bn]
                if len(_same_base)>1:
                    # partial identity — cannot confidently bind bash target to THIS file
                    model.corroborated.setdefault(cid, "ambiguous bash-target association: a bash command named '"+_bn+"' but multiple files share this basename; cannot confirm THIS file was the one mutated")
                    diag['ambiguous_bash_assoc']=diag.get('ambiguous_bash_assoc',0)+1
                    _evidence_limits.append(EL.make("AMBIGUOUS_BASH_ASSOC",
                        "a mutation-capable shell command named basename '"+_bn+"' but multiple known files share it; cannot bind the mutation to this specific artifact",
                        "COMMAND_DERIVED", artifact=cid, step_index=i))
                else:
                    model.corroborated.setdefault(cid, "unverifiable provenance: read after an unobserved bash command named this file (offline export cannot confirm original vs mutated)")
                    diag['unverifiable_first_reads']=diag.get('unverifiable_first_reads',0)+1
                    _evidence_limits.append(EL.make("UNVERIFIABLE_PROVENANCE",
                        "a mutation-capable shell command referenced this artifact before its first host-observed state; offline export cannot confirm original vs mutated content",
                        "COMMAND_DERIVED", artifact=cid, step_index=i))
            # CONTENT-CHANGE (observable bash mutation), THREE-STATE:
            #   changed      -> external mutation observed (worker_modified)
            #   unchanged    -> only trustworthy if BOTH reads were COMPLETE
            #   indeterminate-> either read truncated: cannot prove unchanged ->
            #                   flag epistemic uncertainty, do NOT claim "no change"
            if cid in _last_read_content and cid not in _edit_since_read:
                prev_fp, prev_complete = _last_read_content[cid]
                fp_differs = (cur_imports != _last_read_imports.get(cid)) or (cur_content_fp != prev_fp)
                both_complete = cur_complete and prev_complete
                if fp_differs:
                    model.worker_modified.add(cid)   # definite change (heads differ)
                    write_note_bash="  [external (bash) mutation observed: content changed between reads]"
                    diag['bash_mutations_observed']=diag.get('bash_mutations_observed',0)+1
                elif not both_complete:
                    # fingerprints match but at least one read was TRUNCATED — the
                    # unseen tail could differ. Honest answer is INDETERMINATE.
                    model.corroborated.setdefault(cid, "content-change indeterminate: a read was truncated; cannot confirm the file is unchanged past the truncation boundary")
                    diag['content_indeterminate']=diag.get('content_indeterminate',0)+1
                    _evidence_limits.append(EL.make("CONTENT_INDETERMINATE",
                        "a read of this artifact was truncated; the unseen tail may differ, so 'unchanged' cannot be established past the truncation boundary",
                        "INFERRED", artifact=cid, step_index=i))
            _last_read_imports[cid]=cur_imports
            _last_read_content[cid]=(cur_content_fp, cur_complete)
            _edit_since_read.discard(cid)
            _bash_mutated_unread.discard(cid)   # it WAS reread -> handled above
            for _b in list(_bash_mutated_unread):
                if P.basename(_b)==P.basename(cid): _bash_mutated_unread.discard(_b)
            _before_edges=len(model.edges)
            model.note_read(cid, body)
            if len(model.edges)>_before_edges: diag['imports_resolved']+=1
        if tool in ("edit","write"):
            orient_reads=0   # progress resets the orientation budget
            _edit_since_read.add(cid)   # FIX: record edit-tool touch so a later
                                        # content change is NOT misattributed to bash
            model.note_edit(cid,"",getattr(step,"full_output",None) or step.output_snippet or "")

        status = model.status(cid) if tgt else "n/a"
        if status in ("grounded","unsupported"): grounded_started=True

        # track verifier outcome (for edit under-fail non-progress evidence)
        if step.test_failed:
            _verifier_failing=True
        elif anchor and anchor.get("green"):
            _verifier_failing=False
        # ---- standard v0.9 signals ----
        # LOOP IDENTITY (fix: distinct canonical files sharing a basename were
        # collapsing to one action fingerprint -> phantom read/edit recurrence).
        # File operations key on the CANONICAL CID; bash keys on the (corrected)
        # command fingerprint. Preserves real same-file loops; kills fake ones.
        loop_key = (tool, cid) if tool in ("read","edit","write") and cid else (tool, act)
        window=recent[-3:]; rep=sum(1 for k in window if k==loop_key)
        _loop_strong=False
        # EDIT-PAYLOAD HISTORY (unconditional): record every successful edit's payload
        # hash so reversion detection works even across the FIRST edit (which has rep=0
        # and would otherwise be skipped). Recorded BEFORE the reversion check reads it
        # for the CURRENT step, so the current nh is compared against PRIOR states only.
        if tool in ("edit","write") and cid:
            _pre_seen = set(_edit_state_hist.get(cid,set()))   # snapshot prior states
            _pre_last = _last_success_nh.get(cid)               # snapshot prior last
        if rep>=1:
            li=min(0.10+0.25*rep,0.85)
            if anchor and anchor.get("green"): li*=0.45
            # WEAK-vs-STRONG edit-loop rule: same-cid EDIT recurrence ALONE is
            # insufficient for STRONG (QUARANTINE-level) tool_loop. Repeated edits
            # get FULL intensity only with a NON-PROGRESS indicator; otherwise WEAK.
            if tool in ("edit","write"):
                oh=getattr(step,"edit_old_h",None); nh=getattr(step,"edit_new_h",None)
                edit_ok = not step.error   # a SUCCESSFULLY applied mutation
                # non-progress indicators:
                #  no_op       : the edit changed nothing (old==new)
                #  oscillation : new content reverts to a prior SUCCESSFUL new-state
                #  under_fail  : editing this cid while the most recent relevant
                #                verifier for it is FAILING (trajectory state, NOT
                #                step.test_failed which is always False on an edit)
                no_op = (oh is not None and oh==nh)
                seen_new = _pre_seen   # PRIOR states only (snapshot before this step)
                # PAYLOAD REVERSION (corrected, panel): nh returns to a PRIOR successful
                # payload AND the LAST successful payload was DIFFERENT (we left it and
                # came back). Same payload re-applied after a read (last==nh, never left)
                # is NOT reversion. This is an edit-PAYLOAD proxy (worker oldString/
                # newString hashes), NOT a whole-file-state claim -- observer certainty
                # does not exceed observer evidence.
                _prev_success = _pre_last
                payload_reversion = (nh is not None and nh in seen_new
                                     and _prev_success is not None and _prev_success != nh)
                under_fail = _verifier_failing
                non_progress = no_op or payload_reversion or under_fail
                if not non_progress:
                    # progressing iterative edits, no failing verifier -> WEAK caution
                    # only (below QUARANTINE persist). observer uncertainty constrains
                    # governor certainty: repeated same-cid edits ALONE != loop.
                    li=min(li,0.30)
                if non_progress:
                    _loop_strong=True   # a STRONG non-progress predicate is met
                # (history recorded unconditionally below, outside the rep gate)
            elif tool in ("webfetch","websearch"):
                # WEB recurrence: identity keys on URL/query (W2). If the SAME target
                # recurs (rep>=2 on this loop_key), that is repeated identical retrieval
                # with NO new information -> a non-progress predicate -> strong-eligible.
                # DISTINCT targets have distinct loop_keys, so rep stays low -> weak.
                if rep>=2:
                    _loop_strong=True   # same target repeated -> non-progress
                else:
                    li=min(li,0.30)     # distinct targets / early -> weak
            else:
                # OTHER non-edit recurrence (bash/read): strong ONLY with genuine
                # non-progress evidence at this step (retry / error / test-fail) OR
                # repeated identical action (rep>=2 on same loop_key = no new outcome).
                if step.error or step.test_failed or step.retries>0 or rep>=2:
                    _loop_strong=True
                else:
                    li=min(li,0.30)
            # ONE observation, ONE contribution: severity UPGRADES the evidence, it
            # does NOT duplicate it. Strong -> emit tool_loop_strong ONLY; weak -> plain
            # tool_loop ONLY. (Avoids double-counting the same causal event in compound.)
            if _loop_strong:
                sig["tool_loop_strong"]=li
            else:
                sig["tool_loop"]=li
        # UNCONDITIONAL edit-payload history record (every successful edit, any rep):
        if tool in ("edit","write") and cid and getattr(step,"edit_new_h",None) and not step.error:
            _edit_state_hist.setdefault(cid,set()).add(step.edit_new_h)
            _last_success_nh[cid]=step.edit_new_h
        if step.retries>0: sig["retry_pressure"]=min(step.retries/4.0,1.0)
        if step.error and not step.test_failed:
            sig["execution_error"]=0.55; open_errors[act]=i
        if (not step.error) and (not step.test_failed) and act in open_errors:
            open_errors.pop(act,None)
            for n in buf.notices:
                if n.signal_type=="execution_error": n.intensity*=0.45
        if i>0 and past_costs:
            srt=sorted(past_costs); base=srt[len(srt)//2]
            if base>0 and step.tokens>2.5*base and step.tokens>1500:
                sig["cost_spike"]=min(step.tokens/8000.0,1.0)
        if i>0 and step.tokens>0: past_costs.append(step.tokens)

        # ---- AUTHORITY-BASED DRIFT ----
        off=False; weak=False
        if tool in ("edit","write"): orient_reads=0   # progress resets orientation budget
        write_note=""
        if tool in ("edit","write"):
            orient_reads=0   # progress resets the orientation budget
            # CREATE vs MODIFY — honestly:
            #   existed_before==True  -> file HAD prior content -> MODIFY (even if
            #                            Clara never observed it; unseen != new).
            #   unseen AND not proven-existing -> treat as CREATE (provisional).
            #   NOTE: WRITE tool / empty-oldString is AMBIGUOUS -> existence UNKNOWN;
            #   we do NOT assume CREATE when existence is unknown-but-unseen.
            existed = getattr(step,"existed_before",None)
            unseen = (cid not in _known_before_step)
            if existed is True:
                is_create = False            # proven pre-existing -> MODIFY
            elif unseen and existed is None and tool=="write":
                is_create = None             # ambiguous: unseen + write -> UNKNOWN
            else:
                is_create = unseen           # unseen edit w/ empty old -> create-ish
            # OFF-REPO SCOPING (W3, corrected): exempt a write from repo
            # unauthorized_write ONLY when a root IS attested AND the path is PROVEN
            # outside it (genuine off-repo tool scratch, e.g. AppData/Temp). A merely
            # UNATTESTED identity (no attested root) is UNATTRIBUTABLE, NOT proven
            # off-repo -> it must FAIL CLOSED (still review-worthy). Distinguishing
            # "proven outside attested root" from "unknown, no root" is the whole
            # point -- do not collapse them (two states, one value).
            import provenance as _Pw, repo_root_mapper as _Mw
            _active_root = getattr(_Pw._ACTIVE_CONFIG, "repo_root", None)
            _proven_off_repo = bool(_active_root) and _Pw.is_unverified(cid)
            if _proven_off_repo:
                write_note="  [OFF-REPO write (tool scratch) — outside attested repo scope]"
            # OPERATION-SENSITIVE WRITE AUTHORITY (CREATE-vs-MODIFY aware):
            elif model.is_write_authorized(cid):
                pass                                   # trusted or authority-eligible path -> PROCEED
            elif is_create is True:
                # CREATE a NEW artifact — provisional scope proposal (normal TDD).
                write_note="  [CREATE new artifact — provisional scope proposal]"
                model.grounded.setdefault(cid, "new artifact created by agent (provisional)")
            elif is_create is None:
                # existence UNKNOWN (unseen + write; the write tool can't prove
                # create vs overwrite). We LACK evidence it's a suspicious modify,
                # so default to PROVISIONAL (like create) rather than flag legit
                # file creation. Only PROVEN prior existence (below) reviews.
                write_note="  [WRITE, prior existence UNKNOWN — provisional]"
                model.grounded.setdefault(cid, "written by agent, existence unknown (provisional)")
            else:
                # MODIFY an existing file with no write authorization -> review.
                sig["unauthorized_write"]=0.55
                write_note="  [UNAUTHORIZED MODIFY: existing file, not write-authorized — review]"
        elif tool=="bash":
            al=act.lower()
            if any(h in al for h in _ONTASK): pass
            elif any(h in al for h in _WANDER):
                off = grounded_started or bool(model.grounded) or model.real_test_ran
        elif tool=="read":
            # DRIFT-ON-READ (panel-corrected): ordinary orientation reads of a
            # CORRECTLY-IDENTIFIED in-repo artifact are insufficient BY THEMSELVES
            # to drive STRONG interpretive-drift escalation (HALT). Only a
            # genuinely DISCONNECTED read -- one whose identity is UNVERIFIED
            # (off-repo / unattested namespace / escaping) -- accumulates toward
            # strong drift. In-repo orientation reads may carry WEAK uncertainty
            # but cannot independently escalate to HALT.
            import provenance as _P
            # cid="." is the attested repo ROOT (a KNOWN identity), NOT unverified.
            _unverified_read = True if not cid else (cid != "." and _P.is_unverified(cid))
            # in-repo orientation = the cid is in the host-attested inventory (or is
            # a resolvable in-repo directory/path, i.e. verified & not escaping).
            # POSITIVE confirmation required: orientation-neutral ONLY when the cid
            # is verifiably in the host-attested inventory (a file) OR is a verified
            # in-repo directory (prefix of inventory files). NO inventory attested,
            # or cid NOT in it -> NOT assumed orientation (uncertainty -> caution).
            _in_repo_orientation = (not _unverified_read) and (
                cid == "." or (bool(host_inventory) and (
                    cid in host_inventory or
                    any(inv_c.startswith(cid + "/") for inv_c in host_inventory))))
            if status=="trusted":
                disconnected=0
            elif status in ("grounded","unsupported") and (model.real_test_ran or bool(model.externally_trusted)):
                if _in_repo_orientation:
                    # In-repo orientation is BOUNDED: a reasonable reconnaissance budget
                    # is NEUTRAL, but SUSTAINED unrelated in-repo expansion without
                    # progress is eventually meaningful drift (panel: in-repo != on-task
                    # forever). The goal target/companions are neutral; unrelated in-repo
                    # reads consume the budget.
                    _ORIENT_BUDGET=4
                    is_target = (cid in model.externally_trusted) or (cid==".")
                    if not is_target:
                        orient_reads+=1
                    if orient_reads>_ORIENT_BUDGET:
                        # over budget -> slow drift accumulation (weaker than off-repo)
                        weak=True
                        if orient_reads>_ORIENT_BUDGET+2:
                            off=True   # sustained wander -> strong drift
                    # within budget -> neutral (pass)
                elif _unverified_read:
                    # genuinely disconnected (off-repo/unattested) -> strong accumulation
                    disconnected+=1
                    if disconnected>=2:
                        model.escalate(cid); off=True
                    else:
                        weak=True
                else:
                    # verified but outside the attested inventory (e.g. a transient/
                    # generated in-tree path not in the pre-run set) -> weak uncertainty
                    weak=True
        if off: sig["interpretive_drift"]=0.40
        elif weak: sig["interpretive_drift"]=0.18
        if "agent_regression" in sig: diag['regressions']+=1

        # eval-surface unchanged
        es=getattr(step,"eval_surface",None); es_note=""
        if es in ("deletion","weakening","modification"):
            sig["eval_surface_mutation"]=0.50
            es_note=f"  [eval-surface {es}: success criterion changed -- human review]"
        elif es=="addition": es_note="  [test added (healthy TDD)]"

        if _unobservable_mutation:
            diag['unobservable']=diag.get('unobservable',0)+1
            _evidence_limits.append(EL.make("UNRESOLVED_MUTATION_TARGET",
                "a mutation-capable shell command executed with an unresolved target (glob/pipe/xargs); the offline trace cannot resolve which artifact(s) were affected, and cannot establish that any filesystem change definitely occurred",
                "COMMAND_DERIVED", step_index=i))
            _unobservable_mutation=False
        for st,inten in sig.items(): buf.add(st,inten,i,policies)
        buf.prune(i,policies)
        uw_flag = any(n.signal_type=="unauthorized_write" and
            n.relevance(i,policies)>=policies["unauthorized_write"].persist_threshold
            for n in buf.notices)
        uw_count = sum(1 for n in buf.notices if n.signal_type=="unauthorized_write")
        # regression -> its own REQUIRE_REVIEW (distinct from drift/eval-surface)
        regression_flag = any(n.signal_type=="agent_regression" and
            n.relevance(i,policies)>=policies["agent_regression"].persist_threshold
            for n in buf.notices)
        note=""
        if step.test_failed: note="  [test red]"
        elif off: note="  [off-task: unauthorized expansion]"
        note+=es_note+write_note
        rec=S.decide(buf,i,note,off,policies)
        # operation-sensitive write escalation (never downgrades an existing HALT)
        if uw_flag and rec.decision.value in ("PROCEED","THROTTLE","VERIFY"):
            uw_notice = next((n for n in buf.notices if n.signal_type=="unauthorized_write"),None)
            recurr = uw_notice.recurrences if uw_notice else 1
            if recurr>=2:
                rec.decision=S.Decision.QUARANTINE
                rec.reason="Repeated unauthorized writes — editing multiple files with no task connection."+note
            else:
                rec.decision=S.Decision.REQUIRE_REVIEW
                rec.reason="Unauthorized write — agent edited a file with no established task connection; human review."+note
        recs.append(rec)
        if want_why:
            whys.append(Why(i,act,status,
                ("real:"+("green" if (anchor or {}).get("green") else "red")) if (anchor and anchor.get("ran")) else "-",
                progress,{k:round(v,2) for k,v in sig.items()},rec.decision.value,rec.reason.strip()))
        recent.append(loop_key)
    diag['files_trusted_via_chain']=sum(1 for v in model.externally_trusted.values() if "imported by trusted" in v)
    if _evidence_limits:
        diag['evidence_limits']=[e.to_dict() for e in _evidence_limits]
    if diagnostic:
        return recs, (whys if want_why else None), diag
    return (recs,whys) if want_why else recs
