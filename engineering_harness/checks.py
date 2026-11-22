"""Gate checks for the Offline Guru engineering harness.

Each function returns::

    {"status": "PASS" | "FAIL" | "BLOCKED",
     "measured": {...} | None,
     "threshold": {...} | None,
     "evidence": ["relative/path", ...],
     "notes": ["..."]}

Rules the checks enforce:

* **No stale ev.** A "final" artifact must carry the current source digest
  (ev.is_current). An artifact from before the last code change cannot
  satisfy a gate.
* **No silent skip.** A missing artifact is BLOCKED, never PASS.
* **No self-approval.** An approval must exist in state/signoffs.json from the
  named reviewer, for that phase, with status approved.
* **Thresholds come from the config and the recorded baseline**, not from
  hard-coded optimism.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

import evidence as ev

H = ev.HARNESS
REPO = ev.REPO

REQUIRED_FINDING_FIELDS = [
    "id", "stage", "severity", "observation", "evidence", "hypothesis",
    "experiment", "result", "conclusion", "confidence", "recommended_action",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _criteria() -> dict[str, Any]:
    return yaml.safe_load((H / "config" / "acceptance_criteria.yaml").read_text())


def _tolerances() -> dict[str, float]:
    return _criteria().get("tolerances", {})


def _result(status: str, measured: Any = None, threshold: Any = None,
            evidence: list[str] | None = None, notes: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "measured": measured,
        "threshold": threshold,
        "evidence": evidence or [],
        "notes": notes or [],
    }


def _artifact(name: str) -> dict[str, Any] | None:
    return ev.load(f"artifacts/{name}.json")


def _baseline(name: str) -> dict[str, Any] | None:
    return ev.load(f"baselines/{name}.json")


def _state(name: str) -> dict[str, Any] | None:
    return ev.load(f"state/{name}.json")


def _fresh(name: str) -> tuple[dict[str, Any] | None, str | None]:
    """Load an artifact and report why it cannot be trusted, if not current."""
    doc = _artifact(name)
    if doc is None:
        return None, f"missing artifacts/{name}.json"
    if not ev.is_current(doc):
        got = (doc.get("_evidence") or {}).get("source_digest")
        return None, (f"artifacts/{name}.json is stale (digest {got}, "
                      f"current {ev.source_digest()}); re-run the benchmark")
    return doc, None


def _report(name: str) -> Path:
    return H / "reports" / name


def _report_lines(name: str) -> int:
    p = _report(name)
    return len(p.read_text(encoding="utf-8").splitlines()) if p.is_file() else 0


def _signoff(phase: int, agent: str) -> dict[str, Any] | None:
    doc = _state("signoffs") or {"signoffs": []}
    matches = [s for s in doc.get("signoffs", [])
               if s.get("phase") == phase and s.get("agent") == agent
               and s.get("status") == "approved"]
    return matches[-1] if matches else None


def _findings() -> list[dict[str, Any]]:
    doc = _state("findings") or {}
    return doc.get("findings", [])


def _fixes() -> list[dict[str, Any]]:
    doc = _state("fixes") or {}
    return doc.get("fixes", [])


def _workflow() -> dict[str, Any]:
    return _state("workflow") or {}


def _junit_cases() -> tuple[list[tuple[str, str]], dict[str, int]]:
    """(classname, name) cases and failure counts from the recorded suite."""
    xml_path = H / "artifacts" / "pytest_final.xml"
    if not xml_path.is_file():
        return [], {"failures": 0, "errors": 0}
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return [], {"failures": 0, "errors": 0}
    suites = [root] if root.tag == "testsuite" else list(root)
    cases: list[tuple[str, str]] = []
    bad = {"failures": 0, "errors": 0}
    for suite in suites:
        for case in suite.iter("testcase"):
            cls = case.get("classname", "")
            name = case.get("name", "")
            cases.append((cls, name))
            if case.find("failure") is not None:
                bad["failures"] += 1
            if case.find("error") is not None:
                bad["errors"] += 1
    return cases, bad


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------
def p1_architecture_inspected() -> dict[str, Any]:
    required = ["PHASE1_REPOSITORY_AUDIT.md", "ARCHITECTURE_MAP.md"]
    have = [n for n in required if _report(n).is_file()]
    if len(have) < 2:
        return _result("BLOCKED", notes=[f"missing report(s): {required}"], evidence=have)
    arch = _report("ARCHITECTURE_MAP.md").read_text(encoding="utf-8")
    lines = {n: _report_lines(n) for n in have}
    modules = ["guru/pipeline.py", "guru/realtime.py", "guru/audio.py", "guru/server/app.py",
               "guru/translate.py", "guru/tts.py", "guru/storage.py", "guru/bus.py"]
    unmentioned = [m for m in modules if m not in arch]
    ok = all(v >= 40 for v in lines.values()) and not unmentioned
    return _result(
        "PASS" if ok else "FAIL",
        measured={"lines": lines, "unmentioned_modules": unmentioned},
        threshold={"min_lines": 40, "modules_documented": modules},
        evidence=[f"reports/{n}" for n in have],
        notes=[] if ok else ["architecture map is too thin or omits core modules"],
    )


def p1_baselines_recorded() -> dict[str, Any]:
    suite = _baseline("pytest_baseline")
    asr = _baseline("asr_wer_baseline")
    lat = _baseline("latency_baseline")
    missing = [n for n, d in (("pytest", suite), ("asr_wer", asr), ("latency", lat)) if not d]
    if missing:
        return _result("BLOCKED", notes=[f"missing baseline(s): {missing}"])
    problems = []
    if suite.get("exit_code") != 0:
        problems.append(f"baseline suite exit {suite.get('exit_code')}")
    if int(suite.get("tests", 0)) < 150:
        problems.append(f"baseline suite only ran {suite.get('tests')} tests")
    if not (0.0 <= float(asr.get("wer", 9)) <= 0.5):
        problems.append(f"implausible baseline WER {asr.get('wer')}")
    if not asr.get("reference") or not (REPO / asr["reference"]).is_file():
        problems.append("ASR baseline does not name an existing reference transcript")
    stages = lat.get("stages") or {}
    if not (stages.get("e2e_text_ms") or {}).get("p95"):
        problems.append("latency baseline has no e2e p95")
    return _result(
        "FAIL" if problems else "PASS",
        measured={
            "pytest": {"exit_code": suite.get("exit_code"), "tests": suite.get("tests")},
            "asr": {"wer": asr.get("wer"), "segments": asr.get("segments"),
                    "model": asr.get("model")},
            "latency": {"p50": (stages.get("e2e_text_ms") or {}).get("p50"),
                        "p95": (stages.get("e2e_text_ms") or {}).get("p95")},
        },
        threshold={"min_tests": 150, "wer_range": [0.0, 0.5]},
        evidence=["baselines/pytest_baseline.json", "baselines/asr_wer_baseline.json",
                  "baselines/latency_baseline.json"],
        notes=problems,
    )


def p1_findings_evidenced() -> dict[str, Any]:
    findings = _findings()
    if not findings:
        return _result("BLOCKED", notes=["state/findings.json has no findings"])
    problems: list[str] = []
    if len(findings) < 4:
        problems.append(f"only {len(findings)} findings; at least 4 expected for this system")
    for f in findings:
        for field in REQUIRED_FINDING_FIELDS:
            if not f.get(field):
                problems.append(f"{f.get('id', '?')} missing {field}")
        if not isinstance(f.get("evidence"), list) or not f["evidence"]:
            problems.append(f"{f.get('id', '?')} has no evidence references")
        else:
            existing = [e for e in f["evidence"] if (H / e).exists() or (REPO / e).exists()]
            if not existing:
                problems.append(f"{f.get('id', '?')} evidence paths do not exist: {f['evidence'][:3]}")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"findings": len(findings),
                  "by_severity": _count_by(findings, "severity"),
                  "by_stage": _count_by(findings, "stage")},
        threshold={"min_findings": 4, "required_fields": REQUIRED_FINDING_FIELDS},
        evidence=["state/findings.json"],
        notes=problems,
    )


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


def p1_root_causes() -> dict[str, Any]:
    findings = _findings()
    if not findings:
        return _result("BLOCKED", notes=["no findings"])
    problems = []
    unresolved = []
    for f in findings:
        status = str(f.get("status", "")).lower()
        if status == "unresolved":
            unresolved.append(f["id"])
            if not f.get("next_experiment"):
                problems.append(f"{f['id']} unresolved without a next experiment")
        elif not str(f.get("root_cause", "")).strip():
            problems.append(f"{f['id']} has no root_cause and is not marked unresolved")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"findings": len(findings), "unresolved": unresolved},
        threshold={"root_cause_or_unresolved": True},
        evidence=["state/findings.json"],
        notes=problems,
    )


def p1_hypotheses_tested() -> dict[str, Any]:
    """Each finding that lists competing explanations must have actually tested
    at least two of them. An explicitly untested hypothesis is allowed only when
    it states why it could not be tested (e.g. no capture hardware); hiding an
    untestable hypothesis would be worse than declaring it."""
    findings = _findings()
    multi = [f for f in findings if isinstance(f.get("hypotheses"), list) and len(f["hypotheses"]) >= 2]
    rejected = sum(1 for f in findings for h in f.get("hypotheses", [])
                   if h.get("verdict") in ("rejected", "refuted"))
    problems = []
    thin = []
    unexplained = []
    for f in multi:
        tested = [h for h in f["hypotheses"] if h.get("tested")]
        if len(tested) < 2:
            thin.append(f["id"])
        for h in f["hypotheses"]:
            if not h.get("tested") and not str(h.get("reason") or "").strip():
                unexplained.append(f"{f['id']}: {h.get('text', '')[:60]}")
    if len(multi) < 2:
        problems.append(f"only {len(multi)} finding(s) compare 2+ hypotheses")
    if rejected < 1:
        problems.append("no hypothesis was rejected anywhere; competing explanations were not really tested")
    if thin:
        problems.append(f"findings with fewer than two tested hypotheses: {thin}")
    if unexplained:
        problems.append(f"untested hypotheses without a stated reason: {unexplained}")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"findings_with_multiple_hypotheses": len(multi),
                  "findings_with_two_tested": sum(
                      1 for f in multi if sum(1 for h in f["hypotheses"] if h.get("tested")) >= 2),
                  "rejected_hypotheses": rejected,
                  "untestable_but_documented": sum(
                      1 for f in findings for h in f.get("hypotheses", [])
                      if not h.get("tested") and str(h.get("reason") or "").strip()),
                  "hypothesis_total": sum(len(f.get("hypotheses", [])) for f in findings)},
        threshold={"min_multi_hypothesis_findings": 2, "min_tested_per_finding": 2,
                   "min_rejected": 1},
        evidence=["state/findings.json", "experiments/"],
        notes=problems,
    )


def p1_repair_plan() -> dict[str, Any]:
    path = _report("PHASE1_FINAL_REPORT.md")
    if not path.is_file():
        return _result("BLOCKED", notes=["reports/PHASE1_FINAL_REPORT.md missing"])
    text = path.read_text(encoding="utf-8")
    problems = []
    for prio in ("P0", "P1", "P2"):
        if not re.search(rf"^#+\s*{prio}\b", text, re.MULTILINE):
            problems.append(f"no {prio} section in the repair plan")
    ids = {f["id"] for f in _findings()}
    referenced = {i for i in ids if i in text}
    if len(referenced) < max(1, len(ids) - 1):
        problems.append(f"repair plan references {len(referenced)}/{len(ids)} findings")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"findings_referenced": len(referenced), "findings": len(ids)},
        threshold={"sections": ["P0", "P1", "P2"]},
        evidence=["reports/PHASE1_FINAL_REPORT.md"],
        notes=problems,
    )


def _signoff_check(phase: int, agent: str, report: str) -> dict[str, Any]:
    """Verify an approval without a circular dependency on the overall gate.

    An approval must (a) exist for this phase, (b) have been signed against the
    source tree as it is now, (c) be backed by a written review, and (d) have
    been given while every *non-signoff* criterion of that phase passed. The
    overall gate status is deliberately not consulted: it cannot be PASS before
    the approvals exist.
    """
    sign = _signoff(phase, agent)
    lines = _report_lines(report)
    problems = []
    if sign is None:
        problems.append(f"no approved signoff from {agent} for phase {phase}")
    if lines < 25:
        problems.append(f"reports/{report} missing or too short ({lines} lines)")
    digest_now = ev.source_digest()
    if sign is not None and sign.get("source_digest") != digest_now:
        problems.append(
            f"signoff was given for digest {sign.get('source_digest')}, current tree is "
            f"{digest_now}; re-review after the change"
        )
    gate_doc = ev.load(f"reports/GATE_PHASE{phase}.json") or {}
    substantive = [
        c for c in gate_doc.get("criteria", [])
        if not c["check"].endswith(("_qa_signoff", "_evaluator_approval"))
    ]
    not_pass = [c["id"] for c in substantive if c["status"] != "PASS"]
    if gate_doc and not_pass:
        problems.append(f"signed off while substantive criteria were not PASS: {not_pass}")
    if not gate_doc:
        problems.append("no gate evaluation on disk for this phase")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"report_lines": lines, "signed_at": (sign or {}).get("signed_at"),
                  "signoff_digest": (sign or {}).get("source_digest"),
                  "current_digest": digest_now,
                  "substantive_criteria": len(substantive),
                  "substantive_not_pass": not_pass},
        threshold={"min_report_lines": 25, "signoff_matches_current_tree": True},
        evidence=[f"reports/{report}", "state/signoffs.json"],
        notes=problems,
    )


def p1_qa_signoff() -> dict[str, Any]:
    return _signoff_check(1, "qa-agent", "QA_PHASE1.md")


def p1_evaluator_approval() -> dict[str, Any]:
    return _signoff_check(1, "evaluator-agent", "EVALUATOR_PHASE1.md")


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------
def p2_checkpoint_exists() -> dict[str, Any]:
    wf = _workflow()
    cks = wf.get("checkpoints", [])
    if not cks:
        return _result("BLOCKED", notes=["no checkpoints recorded"])
    pre = [c for c in cks if c.get("kind") in ("pre-phase2", "manual")]
    if not pre:
        return _result("FAIL", measured={"checkpoints": len(cks)},
                       notes=["no pre-phase2/manual checkpoint predates the fixes"])
    valid = []
    for c in pre:
        manifest = H / "checkpoints" / c["id"] / "manifest.json"
        tar = H / "checkpoints" / c["id"] / "source.tar.gz"
        if manifest.is_file() and tar.is_file():
            doc = ev.load(manifest)
            if doc and doc.get("files"):
                valid.append(c["id"])
    return _result(
        "PASS" if valid else "FAIL",
        measured={"checkpoints": len(cks), "valid": valid},
        threshold={"requires_restorable_snapshot": True},
        evidence=[f"checkpoints/{v}/manifest.json" for v in valid],
        notes=[] if valid else ["checkpoint directories lack a manifest or tarball"],
    )


def p2_fixes_implemented() -> dict[str, Any]:
    findings = _findings()
    fixes = _fixes()
    if not fixes:
        return _result("BLOCKED", notes=["state/fixes.json has no fixes"])
    cases, _bad = _junit_cases()
    case_names = {n for _c, n in cases}
    critical = [f for f in findings if f.get("severity") in ("P0", "P1")
                and f.get("status") in ("resolved", "fixed", "verified")]
    problems = []
    accepted = 0
    for f in critical:
        related = [x for x in fixes if x.get("finding") == f["id"]]
        if not related:
            problems.append(f"{f['id']} marked {f['status']} but has no fix entry")
            continue
        for fix in related:
            if fix.get("status") != "accepted":
                problems.append(f"{fix.get('id')} for {f['id']} is {fix.get('status')}")
                continue
            if fix.get("test_status") != "passed":
                problems.append(f"{fix.get('id')} test_status={fix.get('test_status')}")
                continue
            if not fix.get("tests"):
                problems.append(f"{fix.get('id')} names no regression test")
                continue
            missing = [t for t in fix["tests"] if t.split("::")[-1] not in case_names]
            if missing:
                problems.append(f"{fix.get('id')} tests not found in the recorded suite: {missing}")
                continue
            accepted += 1
    for fix in fixes:
        if fix.get("status") == "accepted":
            for path in fix.get("files", []):
                if not (REPO / path).exists():
                    problems.append(f"{fix['id']} claims to change missing file {path}")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"fixes": len(fixes), "accepted": accepted,
                  "critical_findings": len(critical),
                  "suite_cases": len(case_names)},
        threshold={"every_critical_fix_accepted_and_tested": True},
        evidence=["state/fixes.json", "artifacts/pytest_final.xml"],
        notes=problems,
    )


def p2_full_suite() -> dict[str, Any]:
    doc, why = _fresh("pytest_final")
    if doc is None:
        return _result("BLOCKED", notes=[why or "pytest evidence missing"])
    problems = []
    if doc.get("exit_code") != 0:
        problems.append(f"pytest exit code {doc.get('exit_code')}")
    if int(doc.get("failures", 0)) or int(doc.get("errors", 0)):
        problems.append(f"failures={doc.get('failures')} errors={doc.get('errors')}")
    if int(doc.get("tests", 0)) < 150:
        problems.append(f"only {doc.get('tests')} tests collected; suite looks truncated")
    base = _baseline("pytest_baseline") or {}
    if base.get("tests") and int(doc.get("tests", 0)) < int(base["tests"]):
        problems.append(f"test count fell from {base['tests']} to {doc.get('tests')}")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"exit_code": doc.get("exit_code"), "tests": doc.get("tests"),
                  "failures": doc.get("failures"), "errors": doc.get("errors"),
                  "skipped": doc.get("skipped")},
        threshold={"exit_code": 0, "min_tests": 150},
        evidence=["artifacts/pytest_final.json", "artifacts/pytest_final.xml"],
        notes=problems,
    )


def p2_contract_preserved() -> dict[str, Any]:
    doc, why = _fresh("pytest_final")
    if doc is None:
        return _result("BLOCKED", notes=[why or "pytest evidence missing"])
    cases, bad = _junit_cases()
    if not cases:
        return _result("BLOCKED", notes=["could not parse JUnit XML"])
    required = ["tests.test_ws", "tests.test_api", "tests.test_security",
                "tests.test_pipeline", "tests.test_storage"]
    counts = {mod: sum(1 for cls, _n in cases if cls.startswith(mod)) for mod in required}
    zero = [m for m, c in counts.items() if c == 0]
    problems = [f"no executed tests for {m}" for m in zero]
    if bad["failures"] or bad["errors"]:
        problems.append(f"suite has failures={bad['failures']} errors={bad['errors']}")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"cases_per_contract_module": counts, **bad},
        threshold={"required_modules": required},
        evidence=["artifacts/pytest_final.xml"],
        notes=problems,
    )


def p2_no_critical_regression() -> dict[str, Any]:
    open_critical = [f["id"] for f in _findings()
                     if f.get("severity") == "P0" and f.get("status") not in ("resolved", "fixed", "verified")]
    blocked = [f["id"] for f in _findings() if str(f.get("status", "")).lower() == "blocked"]
    return _result(
        "FAIL" if open_critical else "PASS",
        measured={"open_p0": open_critical, "blocked_findings": blocked},
        threshold={"open_p0": 0},
        evidence=["state/findings.json"],
        notes=[f"unresolved P0: {open_critical}"] if open_critical else
              ([f"blocked findings remain (allowed for phase 2, must appear in the final report): {blocked}"]
               if blocked else []),
    )


def p2_qa_signoff() -> dict[str, Any]:
    return _signoff_check(2, "qa-agent", "QA_PHASE2.md")


def p2_evaluator_approval() -> dict[str, Any]:
    return _signoff_check(2, "evaluator-agent", "EVALUATOR_PHASE2.md")


# ---------------------------------------------------------------------------
# Phase 3
# ---------------------------------------------------------------------------
def p3_asr_quality() -> dict[str, Any]:
    final, why = _fresh("asr_wer_final")
    base = _baseline("asr_wer_baseline")
    if final is None:
        return _result("BLOCKED", notes=[why or "asr final missing"])
    if base is None:
        return _result("BLOCKED", notes=["asr baseline missing"])
    tol = float(_tolerances().get("wer_absolute_regression", 0.01))
    problems = []
    wer_delta = float(final["wer"]) - float(base["wer"])
    if wer_delta > tol:
        problems.append(f"WER regressed {wer_delta:+.3f} (tolerance +{tol:.3f})")
    if int(final.get("segments", 0)) < int(base.get("segments", 0)):
        problems.append(f"segments fell {base.get('segments')} -> {final.get('segments')}")
    if float(final.get("speech_s", 0)) < float(base.get("speech_s", 0)) * 0.97:
        problems.append(f"recognised speech fell {base.get('speech_s')}s -> {final.get('speech_s')}s")
    if final.get("reference") != base.get("reference"):
        problems.append("final WER used a different reference than the baseline")
    indep, why2 = _fresh("asr_independent_final")
    if indep is None:
        problems.append(why2 or "independent-sample evidence missing")
        agreement = None
    else:
        agreement = min(indep.get("agreement", {}).values() or [0.0])
        if agreement < 0.75:
            problems.append(f"independent-sample inter-model agreement {agreement} < 0.75")
        words = (indep.get("models", {}).get("base", {}) or {}).get("words", 0)
        if words < 300:
            problems.append(f"independent sample produced only {words} words")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"wer": final.get("wer"), "baseline_wer": base.get("wer"),
                  "delta": round(wer_delta, 4), "segments": final.get("segments"),
                  "speech_s": final.get("speech_s"), "empty_segments": final.get("empty_segments"),
                  "independent_agreement": agreement},
        threshold={"wer_delta_max": tol, "independent_agreement_min": 0.75},
        evidence=["artifacts/asr_wer_final.json", "baselines/asr_wer_baseline.json",
                  "artifacts/asr_independent_final.json"],
        notes=problems,
    )


def p3_translation_quality() -> dict[str, Any]:
    final, why = _fresh("translation_quality_final")
    base = _baseline("translation_quality_baseline")
    if final is None:
        return _result("BLOCKED", notes=[why or "translation final missing"])
    if base is None:
        return _result("BLOCKED", notes=["translation baseline missing"])
    tol = float(_tolerances().get("translation_metric_absolute_regression", 0.02))
    delta = float(final.get("chrf_mean", 0)) - float(base.get("chrf_mean", 0))
    problems = []
    if delta < -tol:
        problems.append(f"chrF regressed {delta:+.4f} (tolerance -{tol})")
    if int(final.get("entries", 0)) != int(base.get("entries", -1)):
        problems.append(f"evaluation set changed: {base.get('entries')} -> {final.get('entries')}")
    if final.get("backend") != base.get("backend"):
        problems.append(f"backend changed {base.get('backend')} -> {final.get('backend')}")
    if final.get("dataset") != base.get("dataset"):
        problems.append("dataset path changed between baseline and final")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"chrf": final.get("chrf_mean"), "baseline_chrf": base.get("chrf_mean"),
                  "delta": round(delta, 4), "entries": final.get("entries"),
                  "backend": final.get("backend"),
                  "numbers_preserved": final.get("numbers_preserved")},
        threshold={"chrf_delta_min": -tol, "same_dataset": True},
        evidence=["artifacts/translation_quality_final.json",
                  "baselines/translation_quality_baseline.json"],
        notes=problems + ["reference translations are engineer-written; this is a regression "
                          "signal, not a human quality claim"],
    )


def p3_latency() -> dict[str, Any]:
    final, why = _fresh("latency_final")
    base = _baseline("latency_baseline")
    if final is None:
        return _result("BLOCKED", notes=[why or "latency final missing"])
    if base is None:
        return _result("BLOCKED", notes=["latency baseline missing"])
    min_improve = float(_tolerances().get("latency_min_improvement", 0.15))
    fs = (final.get("stages") or {}).get("e2e_text_ms") or {}
    bs = (base.get("stages") or {}).get("e2e_text_ms") or {}
    if not fs.get("p95") or not bs.get("p95"):
        return _result("BLOCKED", notes=["e2e p95 missing from one side"])
    p95_improve = 1 - float(fs["p95"]) / float(bs["p95"])
    p50_improve = 1 - float(fs["p50"]) / float(bs["p50"])
    problems = []
    if p95_improve < min_improve:
        problems.append(f"e2e p95 improved {p95_improve:.1%}, below the {min_improve:.0%} threshold")
    if p50_improve < -0.10:
        problems.append(f"e2e p50 regressed {-p50_improve:.1%}")
    if final.get("wav") != base.get("wav"):
        problems.append("latency measured on a different input file than the baseline")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"p50_ms": fs.get("p50"), "p95_ms": fs.get("p95"),
                  "baseline_p50_ms": bs.get("p50"), "baseline_p95_ms": bs.get("p95"),
                  "p50_improvement": round(p50_improve, 3),
                  "p95_improvement": round(p95_improve, 3),
                  "segments": final.get("segments")},
        threshold={"min_p95_improvement": min_improve, "max_p50_regression": 0.10},
        evidence=["artifacts/latency_final.json", "baselines/latency_baseline.json"],
        notes=problems,
    )


def p3_resources() -> dict[str, Any]:
    doc, why = _fresh("latency_final")
    if doc is None:
        return _result("BLOCKED", notes=[why or "latency final missing"])
    res = doc.get("resources") or {}
    budget = yaml.safe_load((H / "config" / "hardware_profile.yaml").read_text())
    rlim = budget.get("resource_budget", {})
    rss = float(res.get("rss_mb_peak", 0))
    cpu = float(res.get("cpu_pct_mean", 0))
    problems = []
    if rss > float(rlim.get("peak_rss_mb", 1400)):
        problems.append(f"peak RSS {rss} MB over budget {rlim.get('peak_rss_mb')} MB")
    if cpu > float(rlim.get("cpu_mean_pct", 85)):
        problems.append(f"mean CPU {cpu}% over budget {rlim.get('cpu_mean_pct')}%")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"rss_mb_peak": rss, "cpu_pct_mean": cpu},
        threshold=rlim,
        evidence=["artifacts/latency_final.json", "config/hardware_profile.yaml"],
        notes=problems,
    )


def p3_load_test() -> dict[str, Any]:
    doc, why = _fresh("loadtest_30_final")
    if doc is None:
        return _result("BLOCKED", notes=[why or "loadtest artifact missing (30-client run required)"])
    problems = []
    if int(doc.get("clients", 0)) < 30:
        problems.append(f"only {doc.get('clients')} clients")
    if int(doc.get("socket_errors", 0)) > 0:
        problems.append(f"{doc.get('socket_errors')} socket errors")
    published = int(doc.get("segments_published", 0))
    received = int(doc.get("received_min", 0))
    if published == 0:
        problems.append("no segments were published during the load test")
    if received < published:
        problems.append(f"a student missed segments: received_min={received} published={published}")
    if int(doc.get("bus_drops", 0)) > 0:
        problems.append(f"{doc.get('bus_drops')} bus subscriber drops")
    stats = ((doc.get("pipeline") or {}).get("stats") or {})
    drops = sum(int(stats.get(k, 0) or 0) for k in
                ("asr_queue_drops", "enrichment_queue_drops", "tts_queue_drops"))
    if drops:
        problems.append(f"pipeline dropped {drops} item(s)")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"clients": doc.get("clients"), "published": published,
                  "received_min": received, "socket_errors": doc.get("socket_errors"),
                  "bus_drops": doc.get("bus_drops"), "pipeline_drops": drops,
                  "fanout_p95_ms": (doc.get("fanout_spread_ms") or {}).get("p95"),
                  "e2e_p95_ms": ((doc.get("metrics") or {}).get("e2e_ms") or {}).get("p95")},
        threshold={"clients": 30, "drops": 0, "socket_errors": 0},
        evidence=["artifacts/loadtest_30_final.json"],
        notes=problems + ["loopback clients only; this is not a physical-device test"],
    )


def p3_no_regression() -> dict[str, Any]:
    return p2_full_suite()


def p3_reproducible() -> dict[str, Any]:
    doc, why = _fresh("reproducibility")
    if doc is None:
        return _result("BLOCKED", notes=[why or "artifacts/reproducibility.json missing"])
    allow = float(_tolerances().get("repeat_spread_p95_pct", 25))
    entries = doc.get("entries") or []
    problems = []
    if len(entries) < 2:
        problems.append(f"only {len(entries)} repeated benchmark(s)")
    for e in entries:
        if int(e.get("runs", 0)) < 2:
            problems.append(f"{e.get('metric')} has {e.get('runs')} run(s)")
        spread = float(e.get("spread_pct", 999))
        if spread > allow:
            problems.append(f"{e.get('metric')} spread {spread}% > {allow}%")
    return _result(
        "FAIL" if problems else "PASS",
        measured={"entries": entries},
        threshold={"min_runs": 2, "max_spread_pct": allow},
        evidence=["artifacts/reproducibility.json"],
        notes=problems,
    )


def p3_qa_signoff() -> dict[str, Any]:
    return _signoff_check(3, "qa-agent", "QA_PHASE3.md")


def p3_evaluator_approval() -> dict[str, Any]:
    return _signoff_check(3, "evaluator-agent", "EVALUATOR_PHASE3.md")
