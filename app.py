"""
ClaraGate Demo Backend — 0.1.0rc2
Runs the actual sealed r5 authority engine against three demo scenarios.

Startup: verifies runtime + engine integrity before importing any engine module.
/health: reflects actual verified state, not a hardcoded string.
Subprocess errors: return explicit "no valid Clara verdict" rather than pseudo-Clara JSON.
"""

import os
import sys
import json
import hashlib
import subprocess
import tempfile
import time
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claragate-demo")

app = FastAPI(title="ClaraGate Demo API", version="0.1.0rc2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

BASE      = os.path.dirname(os.path.abspath(__file__))
RUNTIME   = os.path.join(BASE, "runtime", "clara_runtime.py")
ENGINE    = os.path.join(BASE, "runtime", "engine")
INSTALLER = os.path.join(BASE, "runtime", "engine_installer_vNext15.py")
SHA_FILE  = os.path.join(BASE, "runtime", "RUNTIME_SHA256")

DEMO_REPO    = "/demo/repo"
VERIFIER_CMD = "python -m pytest -q demo/"


# ── Startup integrity verification (stdlib only, before any engine import) ────

_STARTUP_STATUS = {"verified": False, "reason": "not yet checked", "engine": None}


def _verify_startup():
    """Verify runtime + engine integrity at startup. No engine imports until this passes."""
    try:
        # 1. Verify runtime bytes
        expected_sha = open(SHA_FILE).read().strip()
        actual_sha   = hashlib.sha256(open(RUNTIME, "rb").read()).hexdigest()
        if actual_sha != expected_sha:
            return False, f"runtime sha mismatch: expected {expected_sha[:12]}… got {actual_sha[:12]}…", None

        # 2. Verify engine via clara_runtime's own verify_engine (after runtime bytes are confirmed)
        result = subprocess.run(
            [sys.executable, "-c",
             f"""
import sys
sys.path.insert(0, {repr(BASE)})
sys.path.insert(0, {repr(os.path.dirname(RUNTIME))})
import clara_runtime as RT
ok, reason = RT.verify_engine({repr(ENGINE)}, {repr(INSTALLER)})
import json
print(json.dumps({{"ok": ok, "reason": reason}}))
"""],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        if not data["ok"]:
            return False, f"engine integrity: {data['reason']}", None

        # 3. Get engine version token
        return True, "ok", "sealed_vNext15"

    except Exception as e:
        return False, str(e), None


@app.on_event("startup")
def startup_event():
    ok, reason, engine = _verify_startup()
    _STARTUP_STATUS["verified"] = ok
    _STARTUP_STATUS["reason"]   = reason
    _STARTUP_STATUS["engine"]   = engine
    if ok:
        logger.info(f"ClaraGate demo backend ready — engine: {engine}")
    else:
        logger.error(f"ClaraGate startup integrity FAILED: {reason}")


# ── Contract (built once after verified startup) ──────────────────────────────

_CONTRACT      = None
_CONTRACT_SHA  = None


def _get_contract():
    global _CONTRACT, _CONTRACT_SHA
    if _CONTRACT is not None:
        return _CONTRACT, _CONTRACT_SHA
    sys.path.insert(0, ENGINE)
    import opencode_adapter as AD
    identity = AD._fingerprint("bash", {"input": {"command": VERIFIER_CMD}})
    _CONTRACT = {
        "completion": {
            "verifier": {"identity": identity, "command": VERIFIER_CMD},
            "acceptance_required": False,
        }
    }
    _CONTRACT_SHA = hashlib.sha256(json.dumps(_CONTRACT).encode()).hexdigest()
    return _CONTRACT, _CONTRACT_SHA


# ── Synthetic session builders ─────────────────────────────────────────────────

def _make_session(cwd, exit_code, output):
    info = {"role": "assistant", "finish": "stop"}
    if cwd:
        info["path"] = {"cwd": cwd, "root": cwd}
    return {
        "info": {"id": "ses_demo", "model": {"id": "demo-agent", "providerID": "opencode"}},
        "messages": [{"info": info, "parts": [{
            "type": "tool", "tool": "bash", "callID": "call_demo",
            "state": {
                "status": "completed",
                "input": {"command": VERIFIER_CMD, "timeout": 300000},
                "output": output,
                "metadata": {"exit": exit_code},
            },
        }]}],
    }


SCENARIOS = {
    "cleared": {
        "session_fn": lambda: _make_session(DEMO_REPO, 0, "47 passed in 1.23s"),
        "narrative":  "Agent completed the task. Designated verifier passed.",
    },
    "not_cleared": {
        "session_fn": lambda: _make_session(DEMO_REPO, 1, "1 failed, 46 passed in 1.31s"),
        "narrative":  (
            "Agent completed the task. The requested behavior was substantially achieved. "
            "But the governing authoritative contract remained RED. "
            "Accomplishment did not equal clearance."
        ),
    },
    "fallback": {
        "session_fn": lambda: _make_session(None, 0, "47 passed in 1.23s"),
        "narrative":  (
            "Agent completed the task. Verifier reported passing. "
            "But Clara could not establish where the verifier ran — "
            "the execution context was absent from the session record. "
            "Clara abstained rather than manufacture clearance."
        ),
    },
}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(session_dict):
    """Run the actual Clara engine. Returns verdict dict or raises."""
    contract, sha = _get_contract()
    with tempfile.TemporaryDirectory() as td:
        sess_p = os.path.join(td, "session.json")
        cont_p = os.path.join(td, "contract.json")
        with open(sess_p, "w") as f: json.dump(session_dict, f)
        with open(cont_p, "w") as f: json.dump(contract, f)
        actual_sha = hashlib.sha256(open(cont_p, "rb").read()).hexdigest()

        result = subprocess.run(
            [sys.executable, RUNTIME,
             "--session",      sess_p,
             "--contract",     cont_p,
             "--contract-sha", actual_sha,
             "--inventory",    cont_p,
             "--repo-root",    DEMO_REPO,
             "--engine",       ENGINE,
             "--installer",    INSTALLER,
             ],
            capture_output=True, text=True, timeout=60,
        )

        if result.returncode not in (0, 2, 3):
            raise RuntimeError(f"Clara runtime exited {result.returncode}: {result.stderr[:200]}")

        try:
            verdict = json.loads(result.stdout)
        except Exception:
            raise RuntimeError(f"Clara runtime produced unparseable output: {result.stdout[:200]}")

        return verdict


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":   "ok" if _STARTUP_STATUS["verified"] else "degraded",
        "verified": _STARTUP_STATUS["verified"],
        "engine":   _STARTUP_STATUS["engine"] or "unverified",
        "reason":   _STARTUP_STATUS["reason"],
        "version":  "0.1.0rc2",
    }


class RunRequest(BaseModel):
    scenario: str = "cleared"


@app.post("/demo/run")
def run_demo(req: RunRequest):
    # Refuse to serve if startup integrity failed
    if not _STARTUP_STATUS["verified"]:
        return {
            "scenario": req.scenario,
            "apparatus_error": True,
            "error": f"Demo backend integrity check failed at startup: {_STARTUP_STATUS['reason']}",
            "verdict": None,
        }

    if req.scenario not in SCENARIOS:
        return {
            "scenario": req.scenario,
            "apparatus_error": True,
            "error": f"Unknown scenario: {req.scenario!r}",
            "verdict": None,
        }

    s = SCENARIOS[req.scenario]
    t0 = time.time()
    try:
        verdict = _score(s["session_fn"]())
    except Exception as e:
        # Apparatus failure — not a Clara verdict
        return {
            "scenario":       req.scenario,
            "apparatus_error": True,
            "error":          f"Demo apparatus error — no valid Clara verdict: {e}",
            "verdict":        None,
        }

    return {
        "scenario":       req.scenario,
        "narrative":      s["narrative"],
        "verdict":        verdict,
        "elapsed_s":      round(time.time() - t0, 2),
        "engine":         _STARTUP_STATUS["engine"],
        "version":        "0.1.0rc2",
        "apparatus_error": False,
    }
