"""
Causal Recovery (signal-specific, epoch-based) — panel-corrected model.
Discharges concerns when their SPECIFIC resolution evidence arrives. NOT a generic
"green clears productivity" bucket. Applied as a buffer operation at verifier events.

Per-signal rules:
  tool_loop        -> discharge on AUTHENTIC + RELEVANT green verifier (productive iteration)
  execution_error  -> discharge on resolution of THAT error (same action succeeds);
                      full-suite green CORROBORATES; unrelated green does NOT
  cost_spike       -> NOT discharged by green; decays temporally (no action here)
  unauthorized_write, interpretive_drift, eval_surface_mutation -> NEVER discharged by green

Epoch model: a valid green closes the RELEVANT concerns accumulated since the last
trusted verification; a later RED re-arms. We implement by discharging eligible
notices registered at/before the green step, and letting new post-green concerns
accumulate fresh.

AUTHENTIC: anchor forgery-guard says the test genuinely ran green (not printed).
RELEVANT (v1 conservative): a FULL-SUITE verifier (no narrow single-file target)
broadly resolves productivity concerns; a targeted/narrow verifier gives WEAKER
(no) broad discharge in v1.
"""
DISCHARGEABLE_ON_GREEN = {"tool_loop"}          # broad productivity discharge
ERROR_SIGNAL = "execution_error"                # discharged by its own resolution (handled by kernel already) + corroborated by full green
NEVER_DISCHARGE = {"unauthorized_write","interpretive_drift","eval_surface_mutation","cost_spike"}

def is_full_suite_green(command, authentic_green):
    """RELEVANT + AUTHENTIC gate. v1 conservative: authentic green AND the verifier
    is a full-suite run (pytest without a narrow single-file/module target)."""
    if not authentic_green:
        return False
    c=(command or "").lower()
    if "pytest" not in c and "test" not in c:
        return False
    # narrow target detection: an explicit .py test file or ::node id -> NOT full-suite
    import re
    if re.search(r"pytest\s+\S*test\S*\.py", c): return False   # pytest test_x.py
    if "::" in c: return False                                   # pytest ...::case
    return True   # bare `pytest`, `pytest -q`, `python -m pytest` -> full suite

def apply_green_recovery(buf, step, command, authentic_green):
    """At a verifier event: if AUTHENTIC + RELEVANT full-suite green, discharge the
    tool_loop concerns from the current epoch (registered at/before this step).
    execution_error gets CORROBORATION (moderate reduction) since a full green
    suite implies the broken operations are no longer failing the suite — but only
    a reduction, not full clear (its own-action resolution is the primary path).
    Returns a WHY fragment describing the recovery, or None."""
    if not is_full_suite_green(command, authentic_green):
        return None
    discharged=[]
    for n in list(buf.notices):
        if n.signal_type in DISCHARGEABLE_ON_GREEN and n.step_registered<=step:
            buf.notices.remove(n); discharged.append(n.signal_type)
        elif n.signal_type==ERROR_SIGNAL and n.step_registered<=step:
            # corroboration: moderate reduction, not full clear
            n.intensity*=0.4; discharged.append(ERROR_SIGNAL+"(corroborated)")
    if discharged:
        return f"recovery: authentic full-suite verifier green -> discharged {sorted(set(discharged))} (productivity concerns resolved this epoch)"
    return None

def note_red_rearm(buf, step):
    """A RED verifier re-arms: post-green concerns should accumulate fresh. In this
    buffer model, new notices already register at their step; we mark the epoch by
    NOT discharging anything registered after the last green. (No-op structurally;
    epoch boundary is enforced by step_registered ordering in apply_green_recovery.)"""
    return None
