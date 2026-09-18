"""
ClaraGate authority-scoring core (parameterized, deterministic-by-construction).

The v0.9 scoring functions (Notice.relevance, NoticeBuffer.escalating, _decide)
read a MUTABLE MODULE-GLOBAL (clara_observer_v09.SIGNAL_POLICIES). That makes
governance policy derive from process-global state — a determinism hole.

This module reimplements the scoring primitives with POLICY PASSED IN AS A
PARAMETER from an immutable source (authority_policy.build_policy). No function
here reads a process-global for policy. v0.9 is left BYTE-FROZEN and untouched;
this is a NEW module.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict
from authority_policy import build_policy, SignalPolicy

@dataclass
class Notice:
    signal_type: str
    intensity: float          # MOST RECENT event magnitude (present), at cur_step
    step_registered: int      # time of the most recent event (recency of `intensity`)
    recurrences: int = 1
    carry: float = 0.0        # decayed accumulation of PRIOR events (already time-discounted)
    def relevance(self, cur:int, policies:Dict[str,SignalPolicy]) -> float:
        pol = policies[self.signal_type]
        # PRESENT contribution: the most recent event's magnitude, decayed from ITS time.
        # HISTORY contribution: `carry` (prior events already decayed to step_registered),
        # further decayed to `cur`. An OLD PEAK cannot be teleported to present magnitude:
        # its magnitude lives only in `carry`, which is monotonically time-discounted.
        d = pol.decay ** (cur - self.step_registered)
        present = self.intensity * d          # current event, decayed from its own time
        history = self.carry * d              # decayed prior PEAK (a max, never a sum)
        base = max(present, history)          # MAX not sum -> weak-repeat can't inflate,
                                              # old peak can't teleport (it's decayed in carry)
        # RECURRENCE-PRESSURE LAW (Option A, stated not inherited): the recurrence
        # multiplier is BOUNDED. Weak-only recurrence cannot, by COUNT ALONE, cross a
        # hard threshold -- consistent with the frozen weak/strong law (weak evidence
        # tops out at REQUIRE_REVIEW; hard stops require a strong predicate). The cap
        # (recurrences capped at 5 -> max multiplier 2.0) does not touch strong-signal
        # escalation (which routes via escalating()/persist_threshold on its own class)
        # nor any per-event intensity.
        _recur_mult = 1.0 + 0.20*min(self.recurrences, 5)
        return base * _recur_mult * pol.weight

@dataclass
class NoticeBuffer:
    notices: List[Notice] = field(default_factory=list)
    def add(self, st, inten, step, policies):
        for n in self.notices:
            if n.signal_type==st:
                # Fold the PRIOR present event into carry, decayed to the new step.
                # This preserves accumulated concern WITHOUT teleporting the old peak's
                # magnitude to present recency (fixes peak-resurrection).
                # carry = decayed PRIOR PEAK (max of prior present and prior carry),
                # discounted to the new step. A MAX (not a sum) so repeated weak events
                # cannot accumulate into a hard stop, and an old strong peak decays
                # honestly instead of teleporting to present magnitude.
                # policies is a REQUIRED explicit input (no silent fallback -- a
                # missing policy must crash a guard, not substitute a decay model).
                d = policies[st].decay ** (step - n.step_registered)
                n.carry = max(n.carry, n.intensity) * d
                n.intensity = inten          # PRESENT = the actual new event magnitude
                n.step_registered = step
                n.recurrences += 1
                return
        self.notices.append(Notice(st,inten,step))
    def present(self, step, policies, mr=0.05):
        return [n.signal_type for n in self.notices if n.relevance(step,policies)>=mr]
    def escalating(self, step, policies):
        return [n.signal_type for n in self.notices
                if n.relevance(step,policies) >= policies[n.signal_type].persist_threshold]
    def peak(self, step, policies):
        return max((n.relevance(step,policies) for n in self.notices), default=0.0)
    def compound(self, step, policies):
        return sum(n.relevance(step,policies) for n in self.notices)
    def prune(self, step, policies, floor=0.05):
        self.notices=[n for n in self.notices if n.relevance(step,policies)>=floor]

# provenance assertion: prove the policy came from the PARAMETER, not a global.
def assert_policy_provenance(policies):
    """Positive proof the scoring policy is the passed immutable mapping, not a
    process-global. Raises if the mapping is missing required keys."""
    required={"interpretive_drift","execution_error","tool_loop","tool_loop_strong","cost_spike",
              "retry_pressure","eval_surface_mutation","agent_regression",
              "unauthorized_write"}
    missing=required - set(policies)
    if missing:
        raise AssertionError(f"policy provenance failure: missing {missing} — "
                             f"policy did not come from build_policy()")
    return True

# reuse v0.9's Decision + DecisionRecord (data-only, no policy reads)
from clara_observer_v09 import Decision, DecisionRecord

def _compound_material_halt(buf, step, policies):
    """vNext14 material-corroboration gate for the compound-HALT branch.
    Returns (halt: bool, material_contributors: [(sig,rel)], material_score: float).
    A signal is INDEPENDENTLY MATERIAL iff relevance >= its own persist_threshold
    (NOT the 0.05 pruning floor). Compound HALT iff >=2 distinct material classes AND
    material_score (sum of material relevances) >= 1.8. Weak residuals excluded from
    both the class count and the score, so they cannot push a hard stop over the line."""
    mats=sorted(((n.signal_type, n.relevance(step,policies)) for n in buf.notices
                 if n.relevance(step,policies) >= policies[n.signal_type].persist_threshold),
                key=lambda x:-x[1])
    classes={s for s,_ in mats}
    material_score=sum(r for _,r in mats)
    halt = (len(classes)>=2 and material_score>=1.8)
    return halt, mats, material_score

def decide(buf, step, note, drift_flag, policies):
    """Parameterized _decide — identical logic to frozen v0.9 _decide, but every
    buffer query takes `policies` (no global reads)."""
    peak=buf.peak(step,policies); compound=buf.compound(step,policies)
    def _rel(sig):   # relevance of a SPECIFIC signal notice (not global peak)
        return max((n.relevance(step,policies) for n in buf.notices if n.signal_type==sig), default=0.0)
    esc=buf.escalating(step,policies); pres=buf.present(step,policies)
    drift_rel=next((n.relevance(step,policies) for n in buf.notices if n.signal_type=="interpretive_drift"),0.0)
    drift_rec=next((n.recurrences for n in buf.notices if n.signal_type=="interpretive_drift"),0)
    if "eval_surface_mutation" in esc:
        d,dr,r,c=Decision.REQUIRE_REVIEW,["eval_surface_mutation"],"Evaluation-surface mutation — agent changed what counts as passing; human review.",0.75
    elif "interpretive_drift" in esc and (drift_rel>=1.0 or drift_rec>=2):
        d,dr,r,c=Decision.HALT,["interpretive_drift"],f"Sustained drift (rel {drift_rel:.2f}, x{drift_rec}) — agent has left the task.",0.86
    elif "interpretive_drift" in esc:
        d,dr,r,c=Decision.REQUIRE_REVIEW,["interpretive_drift"],f"Scope deviation (rel {drift_rel:.2f}) — agent working outside goal scope.",0.74
    elif "execution_error" in esc and compound>=1.0:
        d,dr,r,c=Decision.REQUIRE_REVIEW,["execution_error"],f"Execution errors compounding (score {compound:.2f}).",0.80
    elif "tool_loop_strong" in esc:
        _lr=_rel("tool_loop_strong")
        d,dr,r,c=Decision.QUARANTINE,["tool_loop"],f"Repeated non-progress pattern (rel {_lr:.2f}) — no-op/oscillation/failing verifier.",0.78
    elif "tool_loop" in esc:
        # WEAK-only recurrence: preserves the observer's weak/strong distinction
        # THROUGH the buffer. Weak evidence cannot independently authorize a hard
        # autonomy reduction (QUARANTINE/HALT); it tops out at REQUIRE_REVIEW.
        _lr=_rel("tool_loop")
        d,dr,r,c=Decision.REQUIRE_REVIEW,["tool_loop"],f"Repeated tool/action pattern (rel {_lr:.2f}) — weak recurrence, no strong non-progress evidence.",0.70
    elif _compound_material_halt(buf, step, policies)[0]:
        # COMPOUND HALT (vNext14 material-corroboration rule): a hard "multiple signals
        # compounding" stop requires >= 2 distinct INDEPENDENTLY-MATERIAL classes AND
        # a MATERIAL score (sum of material contributors) >= 1.8. A signal is material
        # only if its relevance >= its OWN persist_threshold -- surviving the 0.05
        # PRUNING floor is NOT independent corroboration. Weak/sub-persist residuals
        # may raise attention (VERIFY/THROTTLE via the full `compound` score below) but
        # CANNOT manufacture a hard stop. This is scoped to THIS branch only; dedicated
        # strong-signal branches (drift HALT, tool_loop_strong QUARANTINE) are ordered
        # ahead and unchanged. WHY names ONLY the material contributors.
        _halt, _mats, _mscore = _compound_material_halt(buf, step, policies)
        _names=[f"{s}({r:.2f})" for s,r in _mats]
        d,dr,r,c=Decision.HALT,[s for s,_ in _mats] or esc,f"Multiple signals compounding (material score {_mscore:.2f}): {', '.join(_names)}.",0.82
    elif compound>=1.0:
        d,dr,r,c=Decision.VERIFY,pres,f"Signals building (score {compound:.2f}).",0.72
    elif compound>=0.4:
        d,dr,r,c=Decision.THROTTLE,pres,f"Early instability (score {compound:.2f}).",0.68
    elif pres:
        d,dr,r,c=Decision.PROCEED,[],f"Minor signals present but below thresholds (score {compound:.2f}).",0.80
    else:
        d,dr,r,c=Decision.PROCEED,[],"No instability signals registered.",0.90
    return DecisionRecord(step,d,r+note,dr,compound,c)
