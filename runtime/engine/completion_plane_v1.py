"""ClaraGate Completion Plane v1 (ADR-001 Option C, mechanical).

SEPARATE from the behavior plane. Reads verifier/closure state; emits a completion
SNAPSHOT. Emits NO behavior-plane decision and MUST NOT influence PROCEED..HALT.

v1 is MECHANICAL: no NLP, no claim-detection. The agent's final prose is preserved as
evidence, never interpreted. An agent claim clears NOTHING; only AUTHORIZED discharge
evidence transitions OPEN -> SATISFIED.

Concepts (ADR-001):
  VERIFIER RECOGNITION  -- what counts as a verification event (existing test_anchor).
  AUTHORITY DESIGNATION -- which verifier has standing (from the task/eval contract;
                           NOT the command name). Passed in as `authoritative_verifier`.
  OBLIGATION STATE      -- OPEN / SATISFIED / WAIVED (normative; NO decay).
  COMPLETION CLEARANCE  -- CLEARED / NOT_CLEARED at a closure boundary.
"""
from dataclasses import dataclass, field
from typing import Optional, List

OPEN, SATISFIED, WAIVED = "OPEN", "SATISFIED", "WAIVED"
CLEARED, NOT_CLEARED = "CLEARED", "NOT_CLEARED"

@dataclass
class VerificationObligation:
    verifier_id: str            # designated authoritative verifier (from contract)
    status: str = OPEN          # OPEN | SATISFIED | WAIVED  (NO relevance, NO decay)
    opened_at: Optional[int] = None
    last_result: Optional[str] = None   # 'RED' | 'GREEN'
    discharged_at: Optional[int] = None

@dataclass
class CompletionSnapshot:
    closure_step: int
    obligations: List[VerificationObligation]
    clearance: str              # CLEARED | NOT_CLEARED
    agent_final_text: str = ""  # preserved as EVIDENCE, never interpreted in v1
    # explicit: this snapshot is authority-plane ONLY; it carries NO behavior decision.

def _is_authoritative(step, authoritative_verifier_id):
    """A step is an authoritative verification event iff it ran the DESIGNATED verifier
    (EXACT canonical identity match -- NOT substring) AND produced a recognized test
    outcome (test_anchor). Authority binds to IDENTITY, not lexical resemblance
    (the git-identity lesson applied to verification authority).
    The canonical id is the adapter's action fingerprint, e.g. 'bash:python_module:pytest'."""
    anc=getattr(step,"test_anchor",None)
    if not anc or not anc.get("ran"): return None
    act=getattr(step,"action","")   # canonical fingerprint from the frozen adapter
    # EXACT identity: the step's canonical action must EQUAL the designated verifier id
    # (or the anchor's explicitly-recorded verifier id). A lookalike substring does NOT
    # inherit standing.
    canonical=act
    anchor_vid=anc.get("verifier") or ""
    if authoritative_verifier_id not in (canonical, anchor_vid):
        return None  # a verification event, but NOT the authoritative one
    return "GREEN" if anc.get("green") else "RED"

def compute_completion(trace, authoritative_verifier_id, closure_step=None,
                       verifier_required=True):
    """Pure function. Walk the trace UP TO the closure boundary; maintain the obligation
    ledger; emit a CompletionSnapshot. Discharge is by AUTHORIZED evidence only.

    FIX 1 (contract creates the obligation): if verifier_required (the task contract
    designates an authoritative verifier), the obligation is OPEN FROM INCEPTION. It is
    NOT created only when the verifier runs. => an agent that NEVER runs the verifier
    ends OPEN/NOT_CLEARED, NOT cleared. (No incentive to skip testing.)
    FIX 3 (closure causality): only steps up to and including closure_step are read.
    A future authorized GREEN AFTER the closure cannot retroactively discharge.

    - authoritative RED  -> obligation OPEN (last_result RED).
    - authoritative GREEN -> obligation SATISFIED (discharge).
    - NON-authoritative check (ad-hoc python -c) -> NOT authoritative -> cannot discharge.
    - agent claim -> NOT scored; preserved as evidence only.
    """
    steps=trace.steps
    if closure_step is None: closure_step=len(steps)-1
    # FIX 1: obligation exists from inception when the contract requires a verifier.
    ob=VerificationObligation(verifier_id=authoritative_verifier_id,
                              status=(OPEN if verifier_required else SATISFIED),
                              opened_at=(0 if verifier_required else None))
    # FIX 3: causality -- do not read beyond the closure boundary.
    for i,s in enumerate(steps[:closure_step+1]):
        r=_is_authoritative(s, authoritative_verifier_id)
        if r is None: continue
        ob.last_result=r
        if r=="RED":
            if ob.status!=OPEN: ob.status=OPEN; ob.discharged_at=None
            if ob.opened_at is None: ob.opened_at=i
        elif r=="GREEN":
            ob.status=SATISFIED; ob.discharged_at=i
    if not verifier_required:
        clearance=CLEARED; obligations=[]      # no obligation designated by the contract
    else:
        clearance = CLEARED if ob.status in (SATISFIED, WAIVED) else NOT_CLEARED
        obligations=[ob]
    final_text=""
    for s in steps[:closure_step+1][::-1]:
        t=getattr(s,"final_text",None)
        if t: final_text=t; break
    return CompletionSnapshot(closure_step=closure_step, obligations=obligations,
                              clearance=clearance, agent_final_text=final_text)
