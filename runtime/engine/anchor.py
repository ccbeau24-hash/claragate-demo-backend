"""
ClaraGate external anchor extraction (v2).

The ONE class of signal the agent does not author: real test-runner behavior.
From a pytest tool step:
  - exit_code   (state.metadata.exit — structured process field the runner
                 produces, NOT free-text the agent narrates. THIS is primary.)
  - collected / passed / failed  (parsed from real pytest summary)

CONFIDENCE TIERS (v2 — panel guidance):
  VERIFIED         real exit code present (banner optional corroboration).
                   The exit lives in tool metadata; forging it means compromising
                   the harness itself, which is outside the threat model.
  WEAK             outcome text present, but NO real exit code AND no banner.
                   Treated as unverified — confers no authority.
  SUSPECTED_FORGERY outcome text ("2 passed") with neither exit nor banner.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import re

_BANNER_RE    = re.compile(r"test session starts")
_PLATFORM_RE  = re.compile(r"platform\s+\w+\s+--\s+Python", re.I)
_COLLECTED_RE = re.compile(r"collected\s+(\d+)\s+item")
_SUMMARY_RE   = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|deselected|xfailed|xpassed)")

VERIFIED="verified"; WEAK="weak"; SUSPECTED_FORGERY="suspected_forgery"; NONE="none"

@dataclass
class TestAnchor:
    ran: bool                    # do we authoritatively believe a real run happened?
    confidence: str              # verified / weak / suspected_forgery / none
    exit_code: Optional[int]
    collected: Optional[int]
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    has_banner: bool = False
    has_exit: bool = False
    forgery_suspected: bool = False

    @property
    def green(self) -> Optional[bool]:
        """Externally-verified pass state, or None if not authoritative."""
        if self.confidence != VERIFIED:
            return None
        if self.exit_code is not None:
            return self.exit_code == 0
        return None

def extract_test_anchor(exit_code, output: str) -> TestAnchor:
    out = output or ""
    has_banner = bool(_BANNER_RE.search(out)) and bool(_PLATFORM_RE.search(out))
    has_exit   = isinstance(exit_code, int)

    cm = _COLLECTED_RE.search(out)
    collected = int(cm.group(1)) if cm else None
    counts = {"passed":0,"failed":0,"error":0,"errors":0,"skipped":0}
    for m in _SUMMARY_RE.finditer(out):
        n, kind = int(m.group(1)), m.group(2)
        if kind in counts: counts[kind]+=n

    has_outcome_text = bool(_SUMMARY_RE.search(out))

    # CONFIDENCE (v2): a REAL EXIT CODE is sufficient for VERIFIED. The exit is a
    # structured metadata field the runner sets; the agent can't fake it without
    # compromising the harness. Banner is optional corroboration.
    if has_exit:
        confidence = VERIFIED
        ran = True
    elif has_banner:
        # banner but no exit — corroborated text, but not the hard signal
        confidence = WEAK
        ran = False
    elif has_outcome_text:
        # "2 passed" typed with neither exit nor banner -> looks forged
        confidence = SUSPECTED_FORGERY
        ran = False
    else:
        confidence = NONE
        ran = False

    return TestAnchor(
        ran=ran, confidence=confidence,
        exit_code=exit_code if has_exit else None,
        collected=collected,
        passed=counts["passed"], failed=counts["failed"],
        errors=counts["error"]+counts["errors"], skipped=counts["skipped"],
        has_banner=has_banner, has_exit=has_exit,
        forgery_suspected=(confidence==SUSPECTED_FORGERY),
    )
