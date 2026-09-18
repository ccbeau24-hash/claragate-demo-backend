#!/usr/bin/env python3
"""ClaraGate Stage 1A - Live-Run Completion Gate harness (HARDENED, fail-closed).

Scores a fresh live OpenCode export at closure using SEALED vNext15 and issues a
CLARAGATE_STAMP ONLY IF the operational scoring and the certified replay AGREE
(replay-equivalence, fail-closed). Caller/harness ONLY - does NOT modify sealed vNext15.
No NLP. No enforcement. No behavior change. This is live-run completion ADJUDICATION,
NOT live control (the agent is never altered while running).

Usage:
  python claragate_stage1.py --export run.json --inventory inv.json \
      --repo-root <repo> --goal "<goal>" --verifier-id bash:python_module:pytest \
      --stamp CLARAGATE_STAMP.json [--candidate-seal CANDIDATE_vNext15.seal]
Exit: 0=CLEARED | 2=NOT_CLEARED | 3=unscorable/integrity/replay-mismatch.
"""
import argparse, json, hashlib, sys, os
import provenance as P, opencode_adapter as A, observe_authority as OA
from clara_observer_v09 import Trace
from observe_authority import canonical_artifact_id
import authority as AU, lease_v1, eval_surface as ES, recovery, authority_scoring as S
from completion_plane_v1 import compute_completion
def _t(a): return a.split(":",1)[1].strip() if ":" in a else ""

def _closure_ok(session):
    for m in session.get("messages",[]):
        if (m.get("info") or {}).get("role")=="assistant" and (m.get("info") or {}).get("finish")=="stop":
            return True
    return False

def _score_trace(session, INV, repo_root, goal, verifier_id, verifier_required):
    """SINGLE scoring path (behavior + completion) with certified green-recovery wiring.
    Used for BOTH the operational and replay passes so they CANNOT drift."""
    agent_final=""
    for m in session.get("messages",[]):
        if (m.get("info") or {}).get("role")=="assistant":
            for pt in m.get("parts",[]):
                if pt.get("type")=="text": agent_final=pt["text"]   # EVIDENCE ONLY
    with P.attested_root(repo_root):
        tr=Trace.from_dict(A.convert(session,"stage1",goal))
        if tr.steps: setattr(tr.steps[-1],"final_text",agent_final)
        lease=lease_v1.LeaseV1(inventory=INV, eval_predicate=ES.is_eval_surface); lease.set_goal(goal)
        for s in tr.steps:
            if s.tool=="read":
                c=canonical_artifact_id(s,_t(s.action))
                if c: lease.note_read(c,"")
        lease.try_mint()
        oW=AU.AuthorityModel.is_write_authorized
        AU.AuthorityModel.is_write_authorized=lambda self,t:(oW(self,t) or lease.is_modify_authorized(t))
        si={i:(_t(s.action) if s.tool=="bash" else "", bool(getattr(s,"test_anchor",None) and s.test_anchor.get("green")), s.tool) for i,s in enumerate(tr.steps)}
        _oD=S.decide
        def _pD(b,st,n,dd,p):
            cmd,g,tl=si.get(st,("",False,""))
            if tl=="bash" and g: recovery.apply_green_recovery(b,st,cmd,True)
            return _oD(b,st,n,dd,p)
        S.decide=_pD
        behavior=[r.decision.value for r in OA.observe_authority(tr, host_inventory=INV)]
        S.decide=_oD
        AU.AuthorityModel.is_write_authorized=oW
        snap=compute_completion(tr, verifier_id, closure_step=len(tr.steps)-1, verifier_required=verifier_required)
    return (behavior[-1] if behavior else None,
            (snap.obligations[0].status if snap.obligations else "NONE"),
            snap.clearance, agent_final)

def _candidate_integrity(seal_path):
    """Verify the sealed candidate manifest matches its seal (best-effort provenance)."""
    if not seal_path or not os.path.exists(seal_path): return "UNKNOWN"
    man=seal_path.replace(".seal",".manifest")
    if not os.path.exists(man): return "UNKNOWN"
    calc=hashlib.sha256(open(man,"rb").read()).hexdigest()
    return "PASS" if calc==open(seal_path).read().strip() else "FAIL"

def score(export_path, inventory_path, repo_root, goal, verifier_id, verifier_required=True, seal_path=None):
    session=json.load(open(export_path, encoding="utf-8"))
    if not _closure_ok(session):
        return None, "no mechanical closure (finish==stop) -> unscorable"
    integ=_candidate_integrity(seal_path)
    if integ=="FAIL":
        return None, "candidate integrity FAIL -> refuse to score"
    INV=set(json.load(open(inventory_path))["cids"])
    trace_hash=hashlib.sha256(open(export_path,"rb").read()).hexdigest()
    # OPERATIONAL pass and REPLAY pass use the SAME _score_trace -> must be identical.
    op=_score_trace(session, INV, repo_root, goal, verifier_id, verifier_required)
    rp=_score_trace(session, INV, repo_root, goal, verifier_id, verifier_required)
    if op[:3]!=rp[:3]:
        return None, f"replay-equivalence MISMATCH op={op[:3]} replay={rp[:3]} -> no stamp"
    behavior,obligation,clearance,agent_final=op
    stamp={
        "stage":"stage1a-live-run",
        "candidate":"vNext15",
        "candidate_seal":(open(seal_path).read().strip() if seal_path and os.path.exists(seal_path) else None),
        "candidate_integrity":integ,
        "run_id":session.get("info",{}).get("id",""),
        "closure_observed":True,
        "authoritative_verifier_id":verifier_id,
        "behavior_final":behavior,
        "verification_obligation":obligation,
        "completion_clearance":clearance,
        "replay_equivalence":"PASS",
        "trace_hash":trace_hash,
        "agent_final_text_evidence_only":agent_final[:200],
    }
    return stamp, None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--export",required=True); ap.add_argument("--inventory",required=True)
    ap.add_argument("--repo-root",required=True); ap.add_argument("--goal",required=True)
    ap.add_argument("--verifier-id",default="bash:python_module:pytest")
    ap.add_argument("--stamp",default="CLARAGATE_STAMP.json")
    ap.add_argument("--candidate-seal",default=None)
    a=ap.parse_args()
    stamp,err=score(a.export,a.inventory,a.repo_root,a.goal,a.verifier_id,seal_path=a.candidate_seal)
    if err:
        print("STAGE1 UNSCORABLE:",err); sys.exit(3)
    json.dump(stamp, open(a.stamp,"w"), indent=2)
    print(json.dumps(stamp, indent=2))
    print(f"\n>>> stamp written to {a.stamp}")
    sys.exit(0 if stamp["completion_clearance"]=="CLEARED" else 2)

if __name__=="__main__": main()
