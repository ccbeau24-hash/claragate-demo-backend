"""Executable buffer-composition invariants (checked by regression.py)."""
def check(NoticeBuffer, decide, build_policy):
    pol=build_policy(); results=[]
    # SINGLE-SIGNAL: one signal class, high magnitude -> NO compound HALT
    b=NoticeBuffer()
    for s in range(20,33): b.add("cost_spike",1.0,s,pol)
    d=decide(b,32,"",0.0,pol)
    results.append(("single_cost_no_hardstop", d.decision.value not in ("HALT","QUARANTINE"), d.decision.value))
    # MULTI-SIGNAL: >=2 independent classes -> compound intervention eligible
    b2=NoticeBuffer(); b2.add("cost_spike",1.0,30,pol); b2.add("execution_error",0.9,31,pol); b2.add("retry_pressure",0.8,32,pol)
    d2=decide(b2,32,"",0.0,pol)
    results.append(("multi_signal_may_escalate", d2.decision.value in ("HALT","QUARANTINE","REQUIRE_REVIEW"), d2.decision.value))
    # PEAK-RESURRECTION: weak-after-strong must not teleport
    b3=NoticeBuffer(); b3.add("cost_spike",1.0,2,pol); b3.add("cost_spike",0.3,32,pol)
    results.append(("peak_resurrection", b3.notices[0].relevance(32,pol)<0.5, round(b3.notices[0].relevance(32,pol),3)))
    # SUSTAINED-SEVERE: three recent 1.0 -> escalating relevance preserved
    b4=NoticeBuffer(); [b4.add("cost_spike",1.0,s,pol) for s in (30,31,32)]
    results.append(("sustained_severe", b4.notices[0].relevance(32,pol)>0.8, round(b4.notices[0].relevance(32,pol),3)))
    # WEAK-RECURRENCE BOUND: 30 weak -> bounded, no hard stop
    b5=NoticeBuffer(); [b5.add("cost_spike",0.3,s,pol) for s in range(1,31)]
    results.append(("weak_recurrence_bounded", b5.notices[0].relevance(30,pol)<1.8, round(b5.notices[0].relevance(30,pol),3)))
    return results

def check_adapter_identity(_fingerprint):
    """PY-C identity fixtures (identity-only; severity left to non-progress rules)."""
    def fp(c): return _fingerprint("bash", {"input":{"command":c}})
    r=[]
    D=['python -c "import a; print(1)"','python -c "import b; print(2)"',
       'python -c "x=3; print(x)"','python -c "import os; print(os.getcwd())"',
       'python -c "import io,sys; import pycodestyle"']
    ids=[fp(c) for c in D]
    r.append(("py_c_distinct_5_identities", len(set(ids))==5, len(set(ids))))
    same=[fp('python -c "import io; run()"') for _ in range(3)]
    r.append(("py_c_same_identity", len(set(same))==1, same[0]))
    inside=fp('python -c "a; b; c"'); outside=fp('python -c "print(1)" ; echo done')
    r.append(("inside_quote_semicolon_one_op", inside.startswith("bash:python_code"), inside))
    r.append(("outside_quote_semicolon_compound", not outside.startswith("bash:python_code"), outside))
    return r

def check_git_identity(_fingerprint):
    """Git command identity fixtures (identity-only; severity left to non-progress).
    Completed polarity: same-subcmd/different-args stay DISTINCT; same exact command
    repeated stays SAME; quoted paths with spaces tokenized correctly."""
    def fp(c): return _fingerprint("bash", {"input":{"command":c}})
    r=[]
    # H5: distinct git subcommands under -C <path> distinct
    D=['git -C /p status; git -C /p diff','git -C /p log --grep=depth',
       'git -C /p remote -v','git -C /p branch']
    r.append(("git_C_distinct_subcommands", len(set(fp(c) for c in D))==len(D), None))
    # PANEL: same subcommand, DIFFERENT args -> DISTINCT
    r.append(("git_log_diff_args_distinct", fp('git log --grep=foo -i')!=fp('git log --grep=bar -i'), None))
    # PANEL: same subcommand, DIFFERENT repo -> DISTINCT
    r.append(("git_status_diff_repo_distinct", fp('git -C /a status')!=fp('git -C /b status'), None))
    # PANEL: quoted path with spaces -> tokenized correctly (finds subcommand)
    r.append(("git_quoted_path_tokenized", fp('git -C "C:/p with space/r" status').startswith("bash:git_status"), fp('git -C "C:/p with space/r" status')))
    # same exact command repeated -> SAME identity (recurrence eligible)
    same=[fp('git log --grep=x -i') for _ in range(3)]
    r.append(("git_same_command_same_identity", len(set(same))==1, same[0]))
    return r

def check_composition_materiality(NoticeBuffer, decide, build_policy):
    """vNext14 material-corroboration fixtures (A/B/C). End-to-end through decide()."""
    from authority_scoring import Notice
    pol=build_policy()
    def mk(sig,target,step):
        w=pol[sig].weight; return Notice(sig, target/(w*1.2), step)
    def halts(notices):
        b=NoticeBuffer(); b.notices=notices
        d=decide(b,26,"",0.0,pol)
        return d.decision.value=="HALT" and "compounding" in d.reason
    r=[]
    A=[mk("cost_spike",1.8,26), mk("execution_error",0.23,26)]
    r.append(("comp_A_cost_dominant_no_halt", not halts(A), None))
    B=[mk("cost_spike",1.0,26), mk("retry_pressure",1.0,26), mk("agent_regression",0.7,26)]
    r.append(("comp_B_three_material_halt", halts(B), None))
    C=[mk("cost_spike",0.76,26), mk("agent_regression",0.78,26), mk("retry_pressure",0.588,26), mk("tool_loop",0.24,26)]
    r.append(("comp_C_material_score_below_no_halt", not halts(C), None))
    return r
