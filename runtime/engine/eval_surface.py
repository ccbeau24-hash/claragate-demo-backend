def is_eval_surface(path):
    """H2 eval-surface predicate. Reuses the kernel classifier's established rules
    (test_*, *_test.py, conftest.py, /tests/ substring) PLUS the minimal T7-required
    correction: recognize `tests` as a path SEGMENT (including leading). Segment
    equality, not substring. No taxonomy expansion beyond this reproduced fix."""
    p = (path or "").lower().replace("\\", "/")
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    base = parts[-1] if parts else ""
    # established kernel rules (unchanged):
    if base.startswith("test_") or base.endswith("_test.py") or base == "conftest.py":
        return True
    if "/tests/" in p:   # retained existing behavior
        return True
    # MINIMAL T7 correction: `tests` as ANY path segment (incl. leading)
    if "tests" in parts[:-1]:   # a directory segment named exactly "tests"
        return True
    return False
