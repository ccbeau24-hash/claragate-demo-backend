"""
GOAL_LINKED_MODIFY_LEASE v1 — PRODUCTION lease.
Built DIRECTLY from the frozen unit constitution, NOT descended
from the HARNESS_REFERENCE_ONLY oracle. Stem-only, persistent per-cid, host-
inventory-required, NO AST. Consumes H1 (identifier extraction) and the injected
eval predicate. Termination UNSPECIFIED in v1 (no revoke/expire).
"""
import h1_extract as H1

class MissingInventoryError(Exception):
    """Required host-attested pre-run inventory missing/invalid -> no lease."""

class LeaseV1:
    def __init__(self, inventory, eval_predicate):
        # INFRA: required host-attested pre-run inventory. None/empty -> refuse.
        if inventory is None or len(inventory) == 0:
            raise MissingInventoryError("required host pre-run inventory missing/empty")
        # canonicalize inventory cids (same rule H1 applies to user paths) so
        # exact-path matching compares like-with-like. Precondition made explicit
        # and enforced HERE rather than assumed.
        self._inventory_raw = set(inventory)
        self._inventory = set(self._canon(c) for c in inventory)
        self._canon_map = {}                      # canon -> original cid (for reporting)
        for c in inventory:
            self._canon_map[self._canon(c)] = c
        self._is_eval = eval_predicate            # injected canonical classifier (H2)
        self._goal = None          # set exactly once (initial user task only)
        self._grounded = set()                    # cids read (host-observed) before edit
        self._dirty = set()                       # cids with pre-mint mutation -> disqualified
        self._created = set()                     # worker-created cids (never pre-existing)
        self._leases = {}                         # cid -> "MODIFY" (persistent)
        self._reasons = {}                        # cid -> structured WHY

    def set_goal(self, text):
        # INITIAL_GOAL_ONLY: the authority-bearing goal is the initial/top-level
        # user task. First write wins; later calls are ignored (a reframed/worker/
        # planner goal must not become a new mint source).
        if self._goal is None:
            self._goal = text or ""

    # --- host-observed events ---
    def note_read(self, cid, content):
        # grounding only. NO content parsing (NO_AST). cid canonicalized.
        self._grounded.add(self._canon(cid))

    def note_bash_mutation(self, cid):
        # a pre-mint mutation event -> dirty; a later read cannot cleanse it (T4).
        self._dirty.add(self._canon(cid))

    def note_create(self, cid):
        self._created.add(self._canon(cid))

    def note_edit(self, cid):
        # An edit of an ALREADY-LEASED cid is an authorized use: lease persists,
        # untouched (T8). An edit of a NOT-YET-LEASED cid is a pre-mint worker
        # modification -> it becomes dirty and can never be minted from that state
        # (no retroactive legalization). This preserves persistence while closing
        # the pre-mint-edit overgrant.
        cid=self._canon(cid)
        if self._leases.get(cid) == "MODIFY":
            return                      # authorized edit of leased cid: persist
        self._dirty.add(cid)            # pre-mint edit: disqualify from minting

    # --- helpers ---
    def _canon(self, cid):
        """canonical cid: forward slashes, strip leading ./ . Same rule as H1's
        path canonicalization, applied to BOTH inventory and user paths."""
        p = (cid or "").replace("\\", "/")
        while p.startswith("./"): p = p[2:]
        return p

    def _stem(self, cid):
        base = cid.replace("\\", "/").split("/")[-1]
        return base[:-3] if base.endswith(".py") else base

    # --- minting ---
    def try_mint(self):
        if not self._goal:
            return
        targets = H1.positive_targets(self._goal)     # H1 (initial goal only)
        neg = H1.prohibited_targets(self._goal)
        for kind, value in targets:
            # T5 negative precedence (neg is stem-keyed; a path's stem may be prohibited)
            stem_of_value = value.split("/")[-1]
            stem_of_value = stem_of_value[:-3] if stem_of_value.endswith(".py") else stem_of_value
            if stem_of_value in neg:
                continue
            if kind == "path":
                # EXPLICIT PATH: exact CANONICAL inventory match. NO basename fallback.
                cv = self._canon(value)
                cid = cv if cv in self._inventory else None
                if cid is None:
                    continue
            else:
                # BARE STEM/CALL: unique stem over the FIXED (canonical) inventory
                matches = [c for c in self._inventory if self._stem(c) == value]
                if len(matches) != 1:
                    continue
                cid = matches[0]
            if cid in self._created:                  # T3 worker-born -> not pre-existing
                continue
            if cid not in self._grounded:             # grounding required before mint
                continue
            if cid in self._dirty:                    # T4 pre-mint dirty
                continue
            if self._is_eval(cid):                    # T7 eval surface excluded
                continue
            # mint persistent MODIFY lease for THIS cid only (T1)
            self._leases[cid] = "MODIFY"
            self._reasons[cid] = {
                "source": "GOAL_LINKED_MODIFY_LEASE",
                "goal_token": value if kind=="stem" else stem_of_value,
                "matched_stem": self._stem(cid),
                "grounded_pre_mint": True,
                "clean_pre_mint": True,
            }

    # --- authorization ---
    def is_modify_authorized(self, cid):
        return self._leases.get(self._canon(cid)) == "MODIFY"

    def lease_allows(self, op, cid):
        # T9: MODIFY only, this cid only. No CREATE, no spread.
        return op == "MODIFY" and self.is_modify_authorized(cid)

    def lease_reason(self, cid):
        return self._reasons.get(self._canon(cid))
