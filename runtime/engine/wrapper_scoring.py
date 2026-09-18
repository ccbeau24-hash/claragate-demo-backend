"""
Wrapper entry point for REAL (OpenCode/Miko) scoring. Fails closed unless BOTH the
host-attested repo root AND the host-attested pre-run inventory are present. NO
legacy last-two-segments heuristic participates in a real run. This is the ONLY
sanctioned path for held-out/production scoring.
"""
import provenance as P, observe_authority as OA
from clara_observer_v09 import Trace
import opencode_adapter as A

class WrapperConfigError(Exception): pass

def score_run(session_dict, goal, repo_root, host_inventory):
    if not repo_root:
        raise WrapperConfigError("real scoring requires an attested repo_root (git toplevel); refuse to guess identity")
    if not host_inventory:
        raise WrapperConfigError("real scoring requires an attested host_inventory (git ls-tree of START_SHA); refuse to score without it")
    with P.attested_root(repo_root):
        trace = Trace.from_dict(A.convert(session_dict, "run", goal))
        return OA.observe_authority(trace, host_inventory=set(host_inventory))
