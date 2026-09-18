"""
ClaraGate authority model (v3).

Grounding (related?) vs Authority (trust?). Trust chains only to external anchors
the agent doesn't author. v3 makes AUTHORITY ELIGIBILITY EXPLICIT on every edge:
an edge carries its resolution status, and propagation flows trust ONLY across
authority-eligible edges. This closes the "unresolved edge leaks authority via
_propagate" bug (found by review).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Set, Optional, List, Tuple
import provenance as P

TRUSTED="trusted"; GROUNDED="grounded"; UNSUPPORTED="unsupported"; UNKNOWN="unknown"

@dataclass
class Edge:
    parent: str
    child: str                # provisional/resolved target key
    resolution: str           # package_path_match | unique_leaf_match | unresolved | unresolved_to_*
    authority_eligible: bool
    control_class: str        # pre_existing | agent_created | agent_influenced
    module_token: str = ""    # the ORIGINAL import token (e.g. "a.utils") — intended identity
    expected_path: str = ""   # canonical package path if the file appears (e.g. "a/utils.py")

@dataclass
class AuthorityModel:
    externally_trusted: Dict[str,str] = field(default_factory=dict)
    grounded: Dict[str,str] = field(default_factory=dict)
    unsupported: Set[str] = field(default_factory=set)
    edges: List[Edge] = field(default_factory=list)   # ALL edges, with eligibility
    real_test_ran: bool = False
    known_files: Set[str] = field(default_factory=set)
    worker_modified: Set[str] = field(default_factory=set)           # files the agent edited
    baseline_imports: Dict[str,Set[str]] = field(default_factory=dict) # imports seen BEFORE any worker edit
    corroborated: Dict[str,str] = field(default_factory=dict)         # host-verified test PARTICIPATION (NOT authority)
    verification_log: list = field(default_factory=list)             # auditable WHY for verification decisions
    resolution_log: Dict[str,int] = field(default_factory=lambda: {"package_path_match":0,"unique_leaf_match":0,"unresolved":0})  # lifetime EVENTS
    # NOTE: resolution_log counts resolution EVENTS over time. For CURRENT graph
    # state use current_edge_state() below.

    def register_file(self, fname):
        if not fname: return
        was_new = fname not in self.known_files
        self.known_files.add(fname)
        if was_new:
            self._reresolve_pending(fname)

    def _reresolve_pending(self, new_file):
        """A file was just observed. Re-run the REAL resolution rule for any
        UNRESOLVED pre-existing edge, using its preserved module_token. An edge
        resolves ONLY if the module token's package path matches new_file, OR the
        leaf is now UNIQUELY resolvable among known files. A same-basename file
        that is NOT the intended package path must NEVER satisfy it."""
        for e in self.edges:
            if e.resolution!="unresolved" or e.control_class!="pre_existing":
                continue
            if new_file in self.worker_modified:
                continue   # a worker-created/modified file can't satisfy a pre-existing edge
            tok=e.module_token
            if not tok:
                continue
            # 1) package-path identity match: a.utils -> a/utils.py == new_file
            if e.expected_path and e.expected_path==new_file:
                e.child=new_file; e.resolution="unresolved_to_package_path_match"; e.authority_eligible=True
                continue
            # 2) unique-leaf ONLY if the token has no package prefix (bare 'utils')
            #    AND exactly one known file has that leaf. A dotted token like
            #    a.utils must match its package path, not any b/utils.py.
            if "." not in tok:
                import provenance as _P
                leaf = tok if tok.endswith(".py") else tok+".py"
                cands=[f for f in self.known_files if _P.basename(f)==leaf]
                if len(cands)==1 and cands[0]==new_file:
                    e.child=new_file; e.resolution="unresolved_to_unique_leaf_match"; e.authority_eligible=True
        self._propagate()

    def _resolve(self, module_token) -> Tuple[str,str]:
        """Map a dotted/plain import token to a real repo file (repo-relative key).
        Returns (key, resolution): package_path_match | unique_leaf_match | unresolved.
        Authority eligibility later treats the first two as eligible."""
        import provenance as _P
        # 1) PACKAGE-PATH match: converters.weight -> converters/weight.py
        pkg_path = module_token.replace(".","/")
        pkg_cand = pkg_path if pkg_path.endswith(".py") else pkg_path+".py"
        if pkg_cand in self.known_files:
            self.resolution_log["package_path_match"]+=1
            return pkg_cand, "package_path_match"
        # 2) UNIQUE LEAF match — ONLY for BARE tokens (no dot). A DOTTED token
        #    (a.utils) carries package structure; if its package path did not
        #    match above, it must NOT fall through to bind a wrong same-basename
        #    file (b/utils.py). Dotted-without-package-match stays UNRESOLVED.
        #    (This is the historical dotted-import authority leak: a.utils ->
        #     b/utils.py via leaf fallback. Fixed here.)
        is_dotted = ("." in module_token)
        leaf = module_token.split(".")[-1]
        leaf_cand = leaf if leaf.endswith(".py") else leaf+".py"
        if not is_dotted:
            leaf_matches = [f for f in self.known_files if _P.basename(f)==leaf_cand]
            if len(leaf_matches)==1:
                self.resolution_log["unique_leaf_match"]+=1
                return leaf_matches[0], "unique_leaf_match"
            if len(leaf_matches)>1:
                self.resolution_log["unresolved"]+=1
                return leaf_cand, "unresolved"   # ambiguous bare leaf -> never guess
        # dotted token, no package-path match -> UNRESOLVED (no leaf fallback)
        self.resolution_log["unresolved"]+=1
        return (pkg_cand, "unresolved")

    def seed_goal(self, goal:str):
        for f in P.filenames_in_goal(goal):
            self.externally_trusted[f]="named in goal (external)"

    def note_real_test(self, anchor:dict, files_in_output):
        if not anchor or not anchor.get("ran"): return
        self.real_test_ran = True
        for f in files_in_output:
            if f in self.worker_modified:
                # VERIFICATION != AUTHORITY. A genuine test traversing a
                # worker-modified file corroborates PARTICIPATION but must not
                # mint external authority (the worker could have made the test
                # traverse it). Auditable WHY.
                self.corroborated[f]="host-verified test participation (worker-modified)"
                self.verification_log.append({
                    "artifact": f, "verification": "genuine pytest participation",
                    "control_history": "worker_modified",
                    "authority_upgrade": "DENIED",
                    "reason": "verification does not create authority"})
                continue
            if f not in self.externally_trusted:
                self.externally_trusted[f]="named by REAL test run (external)"
                self.grounded.pop(f,None); self.unsupported.discard(f)
                self.verification_log.append({
                    "artifact": f, "verification": "genuine pytest participation",
                    "control_history": "not worker-modified",
                    "authority_upgrade": "granted",
                    "reason": "host-attested, no worker influence"})
        self._propagate()

    def _add_edge(self, parent, child, resolution, control_class, module_token="", expected_path=""):
        eligible = (control_class=="pre_existing") and (resolution in ("package_path_match","unique_leaf_match"))
        # agent_created and agent_influenced edges are NEVER authority-eligible
        self.edges.append(Edge(parent,child,resolution,eligible,control_class,module_token,expected_path))

    def note_read(self, target:Optional[str], clean_body:Optional[str]):
        if not target: return
        self.register_file(target)
        source_trusted = target in self.externally_trusted
        imports_now = set(P.imports_of(clean_body or ""))
        # record BASELINE imports the first time we observe this file (i.e. before
        # any worker modification). This is the control-history anchor.
        if target not in self.baseline_imports and target not in self.worker_modified:
            self.baseline_imports[target] = set(imports_now)
        modified = target in self.worker_modified
        baseline = self.baseline_imports.get(target, set())
        for mod in imports_now:
            child, resolution = self._resolve(mod)
            expected = mod.replace(".","/")
            expected = expected if expected.endswith(".py") else expected+".py"
            if modified and mod not in baseline:
                cc = "agent_influenced"
            else:
                cc = "pre_existing"
            self._add_edge(target, child, resolution, cc, module_token=mod, expected_path=expected)
            if child not in self.externally_trusted:
                self.grounded.setdefault(child, f"import edge from {target} ({resolution}, {cc})")
        if target not in self.externally_trusted and target not in self.grounded:
            self.grounded[target]="read by agent (grounding only)"
        self._propagate()

    def note_edit(self, target, old, new):
        """Edits NEVER confer trust. Added imports are AGENT-CREATED edges:
        recorded but authority-INELIGIBLE (anti-laundering). Also marks the file
        WORKER_MODIFIED so later rereads can't launder new imports as pre-existing."""
        if not target: return
        self.worker_modified.add(target)   # mark worker-controlled FIRST
        self.register_file(target)          # (register may trigger re-resolution)
        added=set(P.imports_of(new))-set(P.imports_of(old))
        for mod in added:
            child, resolution = self._resolve(mod)
            self._add_edge(target, child, resolution, "agent_created")  # never eligible
            self.grounded.setdefault(child, f"agent-created import from {target} (grounding only)")

    def _propagate(self):
        """Trust flows ONLY across authority-eligible edges from a trusted parent.
        Explicit invariant — not dependent on which dict an edge sits in."""
        changed=True
        while changed:
            changed=False
            for e in self.edges:
                if (e.authority_eligible and e.parent in self.externally_trusted
                        and e.child not in self.externally_trusted):
                    self.externally_trusted[e.child]=f"imported by trusted {e.parent} ({e.resolution}, eligible)"
                    self.grounded.pop(e.child,None); self.unsupported.discard(e.child)
                    changed=True

    def current_edge_state(self) -> Dict[str,int]:
        """CURRENT resolution state of edges (not lifetime events). This is the
        honest 'how many edges are unresolved right now' for WHY logs."""
        st={"eligible":0,"unresolved":0,"agent_created":0}
        for e in self.edges:
            if e.control_class=="agent_created": st["agent_created"]+=1
            elif e.authority_eligible: st["eligible"]+=1
            else: st["unresolved"]+=1
        return st

    def is_task_connected(self, target) -> bool:
        """RELATEDNESS (grounding): is there ANY evidence this artifact connects
        to the task? Includes non-eligible edges — useful for WHY / reducing
        suspicion. NOT sufficient to authorize mutation (use is_write_authorized).
        """
        if target in self.externally_trusted: return True
        for e in self.edges:
            if e.child==target and e.parent in self.externally_trusted:
                return True   # any edge from a trusted parent shows relatedness
        return False

    def is_write_authorized(self, target) -> bool:
        """WRITE AUTHORIZATION: may the worker MUTATE this artifact without review?
        Requires the target itself be externally trusted, OR an AUTHORITY-ELIGIBLE
        path from trusted scope. Agent-created / agent-influenced edges ground
        relatedness but must NEVER authorize mutation (no self-authorization).
        """
        if target in self.externally_trusted: return True
        for e in self.edges:
            if (e.child==target and e.parent in self.externally_trusted
                    and e.authority_eligible
                    and e.control_class=="pre_existing"):
                return True
        return False

    def status(self, target:Optional[str]) -> str:
        if not target: return TRUSTED
        if target in self.externally_trusted: return TRUSTED
        if target in self.unsupported: return UNSUPPORTED
        if target in self.grounded: return GROUNDED
        return UNKNOWN

    def escalate(self, target):
        if target not in self.externally_trusted:
            self.grounded.pop(target,None); self.unsupported.add(target)