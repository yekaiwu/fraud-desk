"""``docs/demo-script.md`` written from the eval results: every number carries its source cell in
``latest.json`` (or ``results.json``), so the script never quotes a figure the eval did not produce."""

from __future__ import annotations

from typing import Any

from scripts._eval import metrics as mx
from scripts._eval.report import caught_via

VERDICT_LABEL = {
    "refer_edd": "Refer for EDD review",
    "escalate_aml": "Escalate for AML investigation",
    "reject_or_escalate": "Reject or escalate",
    "approve": "Approve",
}
BUNDLE_STORY = {
    "kyc": (
        "KYC",
        "Sembrook Freight Pte. Ltd. declares logistics; the statements show crypto-related counterparties; the ownership chart omits a shareholder; a fourth director is not a registry officer (plan 0.5, deck slide 14).",
    ),
    "aml": (
        "AML",
        "Tanjong Ridge Trading Pte. Ltd. declares S$1,000,000.00 of sales proceeds from one customer; the credits come from three other companies and are forwarded within days (deck slide 15).",
    ),
    "loan": (
        "Loan",
        "Meridian Fastener Supplies Pte. Ltd. prints FY2025 revenue of S$2,400,000.00 against about S$900,000.00 of bank credits; invoices marked paid have no matching receipt; invoices exceed their contract (deck slide 16).",
    ),
}


def _src(path: str) -> str:
    return f"`[{path}]`"


def _acc(R: dict[str, Any], bundle: str) -> dict[str, Any]:
    return next(r for r in R["acceptance"]["digital"] if r["bundle_id"] == bundle)


def _rules_fired(summary: dict[str, Any], floor: str = "medium") -> str:
    rank = mx.SEV_RANK
    best: dict[str, str] = {}
    for f in summary["flags"]:
        if rank[f["severity"]] >= rank[floor] and (
            f["rule"] not in best or rank[f["severity"]] > rank[best[f["rule"]]]
        ):
            best[f["rule"]] = f["severity"]
    return ", ".join(
        f"`{r}` ({s})" for r, s in sorted(best.items(), key=lambda kv: (-rank[kv[1]], kv[0]))
    )


def demo_script_md(R: dict[str, Any]) -> str:
    g = R["generated_with"]
    rt = R["recall_tables"]
    core = rt["core_recall"].get("digital", {})
    fa = R["false_alarms"]["by_render"]
    fa_d = fa.get("digital", {})
    fa_s = fa.get("scan_hq")
    acc = R["acceptance"]
    rank = R["ranking"]["sweep"]
    f02 = next((s for s in rank["shares"] if abs(s["share"] - 0.2) < 1e-9), rank["shares"][0])
    hard = [
        r
        for r in rt["rows"]
        if r["tamper_id"] == "T02b" and r.get("status") == "measured" and r["render"] == "digital"
    ]
    miss = [r for r in rt["rows"] if r["tamper_id"] == "T02c" and r.get("status") == "measured"]
    t33 = [r for r in rt["rows"] if r["tamper_id"] == "T33" and r.get("status") == "measured"]
    can = R["canaries"]
    fair = R["fairness"]
    cost = R["cost"]
    out: list[str] = []
    w = out.append
    w("# Demo script")
    w("")
    w(
        "> Our reading as students, not legal advice. Decision support only; a human decides. All data is fictitious. Written from the eval results: every number carries its source cell in `docs/eval-results/latest.json` (or `results.json`) in square brackets."
    )
    w("")
    w(
        f"Ruleset `{g['ruleset_version']}`, today {g['today']} (injected, never the wall clock), seed {g['seed']}. The generator is ours, so every number below is an **upper bound** on real-world performance (plan 9.4)."
    )
    w("")
    w("## 0. Before you start (two minutes)")
    w("")
    w(
        "1. Terminal 1: `uv sync`, then `uv run python backend/scripts/make_bundle.py --scenario all --variant all --out samples` (the nine bundles) and `uv run python backend/scripts/make_bundle.py --scenario all --variant clean --clean-pool 10 --out samples` for the queue demo."
    )
    w(
        "2. Terminal 1: `GLDB_LLM_BACKEND=fake uv run uvicorn --app-dir backend app.main:app` (no API key is needed for digital bundles; the fake client never leaves the machine)."
    )
    w(
        "3. Terminal 2: `cd frontend && npm install && npm run dev`, open `http://localhost:3000`. If the backend is down use mock mode (section 6)."
    )
    w(
        "4. Say once, at the start: nothing here approves, rejects or escalates anything. A person decides, and every click below is the person."
    )
    w("")
    for sc in ("kyc", "aml", "loan"):
        title, story = BUNDLE_STORY[sc]
        fraud, forger, clean = (
            _acc(R, f"{sc}-fraud"),
            _acc(R, f"{sc}-forger"),
            _acc(R, f"{sc}-clean"),
        )
        fs, fo, cs = fraud["summary"], forger["summary"], clean["summary"]
        n = {"kyc": 1, "aml": 2, "loan": 3}[sc]
        verdict = fraud["human_path"]["verdict"]
        w(f"## {n}. {title} bundle")
        w("")
        w(f"**Story.** {story}")
        w("")
        w("**Clicks.**")
        w("")
        w(
            "1. Upload page (`/upload`): choose use case **"
            + title
            + "**, reference `DEMO-"
            + sc.upper()
            + "-FRAUD`, drop every file from `samples/bundles/"
            + f"{sc}-fraud"
            + "/digital/`, click **Upload**. You land on the case page with the stepper (ingest, classify, extract, forensics, rules, crossdoc, score)."
        )
        w(
            "2. Queue (`/`): the new case shows its risk band, the one-line top finding, coverage and the lane chip. Say: silence is not safety, so coverage is printed beside the score."
        )
        w(
            "3. Open the case. **Findings** tab: expand the first finding, click **Show on page**: the viewer jumps to the page and draws the box on the printed cell."
        )
        w(
            "4. **Documents** tab: point at the document types and the issuer label; open **Received but not checked** (documents no check examined are listed, never hidden)."
        )
        if sc in ("kyc", "aml"):
            w("5. **Graph** tab: the in-case entity graph (companies, directors, counterparties).")
            nxt = 6
        else:
            nxt = 5
        w(
            f"{nxt}. **Review** tab: select the high flags, **Confirm**, type a reason. Pick the verdict **{VERDICT_LABEL.get(verdict, verdict)}** (the lane suggests it, nothing is preselected), type a reason, submit. The case closes; the review log shows who decided and why."
        )
        w(
            f"{nxt + 1}. **Case pack** (new tab): the evidence pack with flags, coverage and the review log."
        )
        w("")
        w("**Expected result** (from the acceptance run, digital):")
        w("")
        fcov = fs["coverage_ratio"]
        w(
            f"- Lane **{fs['lane']}**, band **{fs['band']}**, score **{fs['score']}**, coverage {fcov:.2f} "
            + _src(f"acceptance[{sc}-fraud]: lane, band, score, coverage")
        )
        w(
            f"- Findings at medium or above: {_rules_fired(fs)} "
            + _src(f"acceptance[{sc}-fraud].flags")
        )
        w(
            f"- Scripted analyst: confirmed {fraud['human_path']['confirmed_high_flags']} high flag(s), verdict `{verdict}`, case closed: {fraud['human_path']['closed']} "
            + _src(f"acceptance[{sc}-fraud].human_path")
        )
        w(
            f"- The clean control for the same company stays quiet: lane **{cs['lane']}**, band **{cs['band']}**, score **{cs['score']}** "
            + _src(f"acceptance[{sc}-clean]")
        )
        w(
            f"- The careful-forger variant (every internal identity kept consistent): lane **{fo['lane']}**, band **{fo['band']}**, score **{fo['score']}**; findings: {_rules_fired(fo)} "
            + _src(f"acceptance[{sc}-forger]")
        )
        w("")
    w("## 4. The ranked queue")
    w("")
    w(
        "**Clicks.** Upload six cases of the same kind in any order (for example the clean and fraud bundles of the three use cases from `samples/bundles/`). Open `/`: the default sort is **priority** (status, then score high to low, then coverage low to high). Point at rank 1 to 6 and at the **Fast lane** chip on the clean cases (a person still confirms them). Use the use-case segmented control and the lane filter to show the queue narrowing."
    )
    w("")
    w(
        "**Numbers to quote** (prioritisation test, "
        + str(R["ranking"]["n_slots"])
        + " generated cases per queue, fraud share is our choice, five repetitions):"
    )
    w("")
    a = f02["all"]
    d = f02["detectable"]
    w(
        f"- At a fraud share of {f02['share']:.0%} ({f02['k']} of {rank['n_slots']} cases): top-5 held {a['top5_fraud']['mean']:.1f} fraud cases on average (range {a['top5_fraud']['min']:.0f} to {a['top5_fraud']['max']:.0f}); top-10 held {a['top10_fraud']['mean']:.1f} "
        + _src("ranking.shares[share=0.2].all.top5_fraud, top10_fraud")
    )
    w(
        f"- First fraud case at position {a['first_top_down']['mean']:.1f} working top-down against {a['first_upload']['mean']:.1f} in upload order (random expectation {a['random_first_position']['mean']:.1f}) "
        + _src("ranking.shares[share=0.2].all.first_top_down, first_upload, random_first_position")
    )
    w(
        f"- Without forger cases (the detectable subset) the top-5 held {d['top5_fraud']['mean']:.1f} "
        + _src("ranking.shares[share=0.2].detectable.top5_fraud")
    )
    w(
        f"- Simulated minutes to the first fraud case: {a['sim_minutes_top_down']['mean']:.1f} top-down against {a['sim_minutes_upload']['mean']:.1f} in upload order. This is an **assumed model** ({R['ranking']['sim_model']['source']}), not a timed measurement "
        + _src("ranking.shares[share=0.2].all.sim_minutes_*")
    )
    w(
        "- How it moves with the share: "
        + " ".join(R["ranking"]["movement"][:1])
        + " (all four shares are in the validation report, section 9)."
    )
    w("")
    w("## 5. Numbers to quote, including the misses")
    w("")
    w(
        f"- Core recall on digital tampers: {core['caught']}/{core['n']} = {core['recall']:.2f} (Wilson 95% {core['wilson'][0]:.2f} to {core['wilson'][1]:.2f}) "
        + _src("core_recall.digital")
    )
    for r in hard:
        w(
            f"- **The hard case** (change a deposit and recompute every later balance, T02b): caught in {r['caught']}/{r['n']} instances"
            + (
                f" (by {caught_via(R, 'T02b', 'digital')}: the financial statements still show the old cash and revenue)"
                if caught_via(R, "T02b", "digital")
                else ""
            )
            + ". Say it either way. "
            + _src("recall[T02b,digital]")
        )
    for r in miss:
        w(
            f"- **The known miss** (T02c: the same edit with the financial statements adjusted to match): caught in {r['caught']}/{r['n']}; content checks cannot see a fully consistent story. "
            + _src("recall[T02c,digital]")
        )
    w(
        f"- False alarms on clean bundles: {fa_d['flags_ge_medium']} high or medium flags over {fa_d['n_clean']} digital bundles = {fa_d['per_10']:.2f} per 10 clean bundles "
        + _src("false_alarms.digital")
    )
    if fa_s:
        w(
            f"- **On scanned renders the false alarms are not small**: {fa_s['flags_ge_medium']} high or medium flags over {fa_s['n_clean']} clean scan bundles = {fa_s['per_10']:.1f} per 10. We report it as measured "
            + _src("false_alarms.scan_hq")
        )
        diag = fa.get("scan_pdf")
        if diag:
            w(
                f"- Diagnostic: the same bundles as one image-only PDF per document give {diag['flags_ge_medium']} high or medium flags ({diag['per_10']:.1f} per 10). Uploading a statement as one JPEG per page cannot be put back together; see the false-alarm section of the validation report for the rules that still fire on clean scans "
                + _src("false_alarms.scan_pdf")
            )
    misses = [
        r
        for r in rt["rows"]
        if r.get("status") == "measured"
        and r["render"] == "digital"
        and r["n"]
        and r["caught"] < r["n"]
        and not (r["hard"] or r["known_miss"] or r["weak"])
    ]
    if misses:
        w(
            "- Core tampers with a miss on digital: "
            + "; ".join(f"{r['tamper_id']} {r['caught']}/{r['n']}" for r in misses)
            + " "
            + _src("recall[*,digital]")
        )
    w(
        f"- Deck acceptance (digital, gating): {sum(1 for r in acc['digital'] if r['pass'])}/{len(acc['digital'])} bundles pass; reproducible reruns {acc['reproducible']}/{acc['reproducible_total']} "
        + _src("acceptance, reproducibility")
    )
    w(
        f"- Injection canaries: {can['passed']}/{can['total']} pass (instruction text in a footer, an extra page, white text and a transaction description) "
        + _src("canaries")
    )
    w(
        f"- Fairness slice over six name groups: outcome **{fair['overall']}** against our proposed criteria; disparity of the false-flag rate {fair['disparity']:.2f} "
        + _src("fairness.overall, fairness.disparity")
    )
    wm = max(fair["groups"], key=lambda x: x["wrong_match_rate"] or 0)
    w(
        f"- The weakest fairness number: wrong-match rate (a near-spelled different person that passed) is highest for `{wm['group']}` at {wm['wrong_matches']}/{wm['n_diff']} "
        + _src("fairness.groups[*].wrong_match_rate")
    )
    w(
        "- Cost: "
        + (
            "no real model cost measured (the fake client prices everything at zero); "
            if cost["no_model_cost_measured"]
            else ""
        )
        + f"seconds per digital bundle p50 {cost['seconds']['p50']:.1f}, p95 {cost['seconds']['p95']:.1f} "
        + _src("cost.seconds")
    )
    w("")
    w(
        "**Always say:** the generator is ours, so these are upper bounds; scan numbers use oracle extraction (the generator's own answer sheet stands in for OCR), so they test the rules and the image forensics, not text recognition; the timed analyst test has not been run."
    )
    w("")
    w("## 6. If the live demo fails")
    w("")
    w(
        "1. **Mock mode, no backend:** `cd frontend && npm run dev:mock` (or `NEXT_PUBLIC_USE_MOCK=1 npm run dev`). A banner says MOCK DATA; the twelve fixture cases cover the three bundles, the partial case, the processing stepper and the review flow with the real error codes."
    )
    w(
        "2. **Command line:** `uv run python backend/scripts/run_case.py --scenario loan --variant fraud --timings` prints the findings, the flags and the per-stage seconds with no UI and no network."
    )
    w(
        "3. **Pre-saved packs:** keep the HTML case pack of each fraud bundle (`/cases/{id}/pack?format=html`) from the rehearsal; it prints from a browser and carries the evidence, coverage and review log."
    )
    w(
        "4. **Last resort:** open `docs/eval-results/acceptance.md` and `docs/validation-report.md`; every claim in the talk is in them with its denominator."
    )
    w("")
    w("## 7. Questions you will get")
    w("")
    w(
        '**What about AI-generated documents?** The tool reports provenance hints only: a generator tag in the PDF or image metadata (`ai_provenance_hint`). It says "not detected" when it finds nothing, never that a document is cleared, and pixel detectors are known to fall apart on AI-inpainted numbers (plan 1.3). '
        + (
            f"In our tamper test (a Creator entry naming an image generator, T33) the hint was found in {t33[0]['caught']}/{t33[0]['n']} instances, so do not promise it "
            + _src("recall[T33,digital]")
            + "."
            if t33
            else "That tamper row was not run."
        )
    )
    w("")
    w(
        "**Does it decide?** No. The verdict form never preselects a choice, a reason of at least five characters is mandatory, dismissing a high flag needs a second reviewer, and the audit log is append-only. The tool does not file a suspicious transaction report."
    )
    w("")
    w(
        "**Would this work on real bank statements?** We do not know. The four statement layouts are invented, the registry is a mock file shaped like an ACRA profile, and the real sponsor documents may arrive as scans."
    )
    w("")
    w("## Appendix: timed analyst test protocol (the test itself is a manual task)")
    w("")
    w(
        'File: `docs/eval-results/timed-test.csv`, header `tester,order,queue_id,rep,seconds_to_first_fraud,cases_opened,flags_read,false_flags_dismissed,seconds_per_flag,notes`. Two or three teammates each work one queue (fraud share 20%, repetition 0) top-down in the UI and a different repetition in upload order, order counterbalanced, stopwatch, UI only; the first-fraud time stops at the confirm click on a true flag. Until the file has rows the eval says "not run" and uses the assumed minutes model. With two or three testers this is an anecdote, not a study.'
    )
    return "\n".join(out) + "\n"
