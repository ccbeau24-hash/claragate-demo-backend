"""
ClaraGate structured evidence_limits.

Separates three concepts the wrapper (and Miko) must see distinctly:
  decision       — what Clara allows next (PROCEED/VERIFY/REVIEW/...)
  reason         — WHY that governance decision was selected
  evidence_limits— what Clara COULD NOT establish (epistemic honesty)

An EvidenceLimit is a STRUCTURED record (not prose), so the wrapper can inspect,
filter, and report limitations separately from decisions. It NEVER claims more
than the evidence supports (e.g. D says a mutation-capable command RAN, not that
a filesystem change is host-attested).

Codes + scopes (fixed schema):
  UNVERIFIABLE_PROVENANCE    artifact-scoped     — first read after a mutation-
                             capable shell command named this artifact; offline
                             export cannot confirm original vs mutated.
  UNRESOLVED_MUTATION_TARGET event/run-scoped    — mutation-capable command ran;
                             affected artifact(s) not resolvable offline.
  CONTENT_INDETERMINATE      observation-scoped  — a read was truncated; cannot
                             confirm the file is unchanged past the boundary.
  AMBIGUOUS_BASH_ASSOC       association-scoped   — a bash command named a basename
                             shared by multiple known files; cannot bind to one.
Evidence level: COMMAND_DERIVED | HOST_ATTESTED | INFERRED.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List

_SCOPE = {
    "UNVERIFIABLE_PROVENANCE":    "artifact",
    "UNRESOLVED_MUTATION_TARGET": "event",
    "CONTENT_INDETERMINATE":      "observation",
    "AMBIGUOUS_BASH_ASSOC":       "association",
    "UNVERIFIABLE_EVAL_DEPENDENCY": "evaluation",
}

@dataclass(frozen=True)
class EvidenceLimit:
    code: str                       # one of _SCOPE keys
    basis: str                      # what evidence supports the uncertainty
    evidence_level: str             # COMMAND_DERIVED | HOST_ATTESTED | INFERRED
    artifact: Optional[str] = None  # set for artifact/observation/association scope
    step_index: Optional[int] = None
    @property
    def scope(self): return _SCOPE.get(self.code, "unknown")
    def to_dict(self):
        d=asdict(self); d["scope"]=self.scope
        return {k:v for k,v in d.items() if v is not None}

def make(code, basis, evidence_level, artifact=None, step_index=None):
    if code not in _SCOPE:
        raise ValueError(f"unknown evidence-limit code: {code}")
    return EvidenceLimit(code, basis, evidence_level, artifact, step_index)
