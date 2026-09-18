"""
ClaraGate authority-scoring policy — IMMUTABLE source of truth.

Determinism contract: governance policy must NOT derive from mutable process-
global state. This module defines the policy set as immutable literals in TWO
explicitly-labelled regions:

  V09_BASE_POLICIES  — the frozen v0.9 base signals. Parity-tested against the
                       byte-frozen clara_observer_v09 artifact (see
                       test_policy_parity.py). Changing v0.9 must fail parity.
  OBSERVER_EXTENSIONS— signals ADDED by the authority observer (agent_regression,
                       unauthorized_write). NOT part of v0.9; additive-only;
                       parity deliberately ignores these.

build_policy(config) is a PURE builder: same config -> same mapping, no side
effects, no reads of any process-global. The mapping is PASSED INTO scoring;
nothing installs it into a shared module dict.
"""
from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType

@dataclass(frozen=True)
class SignalPolicy:
    persist_threshold: float
    weight: float
    decay: float

# --- REGION 1: frozen v0.9 base (parity-tested against the frozen artifact) ---
V09_BASE_POLICIES = MappingProxyType({
    "interpretive_drift":    SignalPolicy(0.35, 1.50, 0.75),
    "execution_error":       SignalPolicy(0.45, 1.35, 0.80),
    "tool_loop":             SignalPolicy(0.55, 1.00, 0.88),
    "tool_loop_strong":      SignalPolicy(0.55, 1.00, 0.88),
    "cost_spike":            SignalPolicy(0.60, 0.90, 0.90),
    "retry_pressure":        SignalPolicy(0.65, 0.70, 0.92),
    "eval_surface_mutation": SignalPolicy(0.45, 1.20, 0.85),
})

# --- REGION 2: observer-only extensions (NOT v0.9; additive; parity ignores) ---
OBSERVER_EXTENSIONS = MappingProxyType({
    "agent_regression":   SignalPolicy(0.40, 1.30, 0.85),
    "unauthorized_write": SignalPolicy(0.40, 1.35, 0.85),
})

def build_policy(config=None) -> dict:
    """PURE per-run policy builder. Returns a fresh dict combining the immutable
    v0.9 base and the observer extensions. `config` reserved for declared inputs
    (e.g. case policy lives in provenance; future policy toggles would enter here)
    — but the builder has NO side effects and reads NO process-global."""
    pols = {}
    pols.update(V09_BASE_POLICIES)
    pols.update(OBSERVER_EXTENSIONS)
    return pols
