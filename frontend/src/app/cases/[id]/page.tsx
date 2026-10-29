"use client";

import { Keyboard, Stamp } from "lucide-react";
import { notFound, useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { Modal } from "@/components/Modal";
import { AddDocuments } from "@/components/case/AddDocuments";
import { CaseActions } from "@/components/case/CaseActions";
import { CaseBanners } from "@/components/case/CaseBanners";
import { DeleteDialog, RerunDialog } from "@/components/case/CaseDialogs";
import { CaseHeader } from "@/components/case/CaseHeader";
import { useCaseNeighbours } from "@/components/case/CaseNeighbours";
import { CaseErrorNotice, CaseLoadError, CaseSkeleton } from "@/components/case/CaseStates";
import { DecisionSheet } from "@/components/case/DecisionSheet";
import { AnalysisTrailSheet } from "@/components/explain/AnalysisTrail";
import { CoveragePanel } from "@/components/case/CoveragePanel";
import { UncheckedPagesPanel } from "@/components/case/UncheckedPagesPanel";
import { DocumentsPanel, NotCheckedList } from "@/components/case/DocumentsPanel";
import { EntityGraph } from "@/components/case/EntityGraph";
import { FindingsPanel, type DecideTarget, type SelectOpts } from "@/components/case/FindingsPanel";
import { FlagDecisionDialog } from "@/components/case/FlagDecisionDialog";
import { PageViewer } from "@/components/case/PageViewer";
import { ProcessingStepper } from "@/components/case/ProcessingStepper";
import { RecommendationHero } from "@/components/case/RecommendationHero";
import { ReviewPanel, formUnavailableReason } from "@/components/case/ReviewPanel";
import { Button } from "@/components/ui/Button";
import { Kbd } from "@/components/ui/Kbd";
import { Notice } from "@/components/ui/Notice";
import { Stagger } from "@/components/ui/Stagger";
import { TabPanel, Tabs, type TabItem } from "@/components/ui/Tabs";
import { getCase, getVersions, rerunCase } from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import { documentFirstPage, numberFlags, uncheckedPageList } from "@/lib/flags";
import { deriveEntityGraph, graphApplies, type GEdge } from "@/lib/graph";
import type { CaseDetail, Region } from "@/lib/types";
import type { CaseNav } from "@/components/case/CaseHeader";
import { useIsLg } from "@/lib/useMedia";

type Loc = { fileId: string; page: number };
const GROUP = "case";

/** `ranked` is the numbered flag list (highest concern first), so the viewer opens on the page behind the top concern. */
function initialLoc(c: CaseDetail, flagId: string | null, ranked: readonly { flag: { region?: Region | null; severity?: string } }[]): Loc {
  const flags = c.flags;
  const sel = flagId ? flags.find((f) => f.id === flagId) : undefined;
  // Informational flags often tint a whole page, so only a real concern decides where the viewer opens;
  // with none, it opens on the first page of the first document.
  const r = sel?.region ?? ranked.find((n) => n.flag.region && n.flag.severity !== "info")?.flag.region ?? null;
  if (r) return { fileId: r.file_id, page: r.page };
  return { fileId: c.files[0]?.id ?? "", page: 1 };
}

function CaseBody({ c, versionParam, jobId, reload, reloadVersions, versions, latest, nav }: {
  nav: CaseNav | null;
  c: CaseDetail; versionParam: number | undefined; jobId: string | null; reload: () => Promise<unknown>; reloadVersions: () => Promise<unknown>;
  versions: Awaited<ReturnType<typeof getVersions>>["versions"]; latest: number | undefined;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const isLg = useIsLg();
  const isCurrent = latest === undefined ? versionParam === undefined : c.version === latest;
  const numbered = useMemo(() => numberFlags(c.findings, c.flags), [c.findings, c.flags]);
  const showGraph = graphApplies(c);
  const graph = useMemo(() => (showGraph ? deriveEntityGraph(c) : null), [c, showGraph]);

  const pending = c.pending_second_approvals.length;
  const tabs = useMemo(() => {
    const t: TabItem[] = [{ id: "findings", label: "Findings", count: c.findings.filter((f) => f.severity !== "info").length }];
    if (!isLg) t.push({ id: "page", label: "Page" });
    t.push({ id: "documents", label: isLg ? "Documents" : "Docs" });
    if (showGraph) t.push({ id: "graph", label: "Graph" });
    t.push({ id: "review", label: "Decision & activity", count: pending > 0 ? pending : undefined });
    return t;
  }, [c.findings, isLg, showGraph, pending]);

  const urlTab = params.get("tab") ?? "findings";
  const urlFlag = params.get("flag");
  const [tabState, setTabState] = useState(urlTab);
  const [flagId, setFlagId] = useState<string | null>(urlFlag && c.flags.some((f) => f.id === urlFlag) ? urlFlag : null);
  const [loc, setLoc] = useState<Loc>(() => initialLoc(c, urlFlag, numbered.all));
  const tab = tabs.some((t) => t.id === tabState) ? tabState : "findings";

  const [decide, setDecide] = useState<DecideTarget | null>(null);
  const [decisionOpen, setDecisionOpen] = useState(false);
  const [rerunOpen, setRerunOpen] = useState(false);
  const [docsOpen, setDocsOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [trailOpen, setTrailOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [recorded, setRecorded] = useState<string | null>(null);

  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      const p = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null) p.delete(k);
        else p.set(k, v);
      }
      const qs = p.toString();
      router.replace(`/cases/${c.id}${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [params, router, c.id],
  );
  const goTab = useCallback(
    (id: string) => {
      setTabState(id);
      setParams({ tab: id === "findings" ? null : id });
    },
    [setParams],
  );

  const select = useCallback(
    (id: string | null, opts?: SelectOpts) => {
      setFlagId(id);
      setParams({ flag: id });
      if (!id) return;
      const nf = numbered.byId.get(id);
      if (!nf) return;
      const r = nf.flag.region;
      const target: Loc | null = opts?.to ?? (r ? { fileId: r.file_id, page: r.page } : (() => { const d = documentFirstPage(c, nf.flag.document_ids[0] ?? ""); return d; })());
      if (target) setLoc(target);
      if (opts?.reveal && !isLg) goTab("page");
      else if (!opts?.reveal && isLg && tab !== "findings") goTab("findings");
    },
    [numbered, c, isLg, goTab, tab, setParams],
  );

  const showPage = useCallback(
    (fileId: string, page: number) => {
      setLoc({ fileId, page });
      if (!isLg) goTab("page");
    },
    [isLg, goTab],
  );
  const showDocument = useCallback(
    (docId: string) => {
      const nf = numbered.all.find((x) => x.flag.document_ids.includes(docId) && x.flag.region);
      if (nf) return select(nf.flag.id, { reveal: true });
      const d = documentFirstPage(c, docId);
      if (d) showPage(d.fileId, d.page);
    },
    [numbered, c, select, showPage],
  );
  const selectEdge = useCallback(
    (e: GEdge) => {
      const flagged = numbered.all.find(({ flag }) => flag.table === "transactions" && e.rows.some((r) => r.row === flag.row && flag.document_ids.includes(r.docId)));
      if (flagged) return select(flagged.flag.id, { reveal: true });
      const first = e.rows[0];
      if (first) {
        const doc = c.documents.find((d) => d.id === first.docId);
        const row = doc?.tables.transactions?.rows.find((r) => r.index === first.row);
        const box = row?.box ?? Object.values(row?.cells ?? {}).find((x) => x.box)?.box ?? null;
        if (box) return showPage(box.file_id, box.page);
      }
      if (e.docIds[0]) showDocument(e.docIds[0]);
    },
    [numbered, c, select, showPage, showDocument],
  );

  // keyboard: j/k flags, [ ] pages, Esc clear (never while typing or with a dialog open)
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const t = ev.target as HTMLElement | null;
      if (!t || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
      if (document.querySelector("dialog[open]")) return;
      const all = numbered.all;
      const idx = flagId ? all.findIndex((x) => x.flag.id === flagId) : -1;
      if (ev.key === "j" && all.length) select(all[Math.min(all.length - 1, idx + 1)].flag.id);
      else if (ev.key === "k" && all.length) select(all[Math.max(0, idx < 0 ? 0 : idx - 1)].flag.id);
      else if (ev.key === "[" || ev.key === "]") {
        const file = c.files.find((f) => f.id === loc.fileId);
        if (file) setLoc({ fileId: file.id, page: Math.min(file.pages.length, Math.max(1, loc.page + (ev.key === "]" ? 1 : -1))) });
      } else if (ev.key === "Escape" && flagId) select(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [numbered, flagId, select, c.files, loc]);

  const goFindings = useCallback(() => {
    goTab("findings");
    document.getElementById("findings-region")?.scrollIntoView?.({ block: "start" });
  }, [goTab]);

  const goUnreviewed = useCallback(() => {
    goTab("findings");
    // The findings tab may only just have been shown: wait a frame before scrolling to the list.
    window.setTimeout(() => document.getElementById("unreviewed-pages")?.scrollIntoView?.({ block: "start", behavior: "smooth" }), 60);
  }, [goTab]);

  const afterReview = useCallback(
    (entryId: string) => {
      setRecorded(`Recorded as ${entryId}`);
      void reload();
      void reloadVersions();
    },
    [reload, reloadVersions],
  );
  const startedNewVersion = useCallback(
    (jobIdNew: string) => {
      setRerunOpen(false);
      setDocsOpen(false);
      setFlagId(null);
      const p = new URLSearchParams();
      p.set("job", jobIdNew);
      router.replace(`/cases/${c.id}?${p.toString()}`);
      void reload();
      void reloadVersions();
    },
    [router, c.id, reload, reloadVersions],
  );
  async function doRerun() {
    setBusy(true);
    setActionError(null);
    try {
      const r = await rerunCase(c.id);
      startedNewVersion(r.job_id);
    } catch (e) {
      setRerunOpen(false);
      setActionError(e);
    } finally {
      setBusy(false);
    }
  }

  const why = formUnavailableReason(c, isCurrent);
  const canDecide = why === null;
  const canFlagDecide = isCurrent && c.status === "open";
  const selectedNf = flagId ? numbered.byId.get(flagId) ?? null : null;
  const currentHref = `/cases/${c.id}`;

  if (c.status === "processing") {
    return (
      <Stagger className="space-y-6">
        <CaseHeader c={c} isCurrent={isCurrent} versions={versions} onVersion={() => {}} nav={nav} />
        <ProcessingStepper
          jobId={jobId}
          onFinished={() => {
            void reload();
            setParams({ job: null });
          }}
          onRerun={() => void doRerun()}
        />
        <div className="flex justify-end">
          <div className="w-full sm:w-56">
            <CaseActions c={c} isCurrent={isCurrent} onRerun={() => {}} onAddDocs={() => {}} onDelete={() => setDeleteOpen(true)} variant="ghost" />
          </div>
          <DeleteDialog open={deleteOpen} c={c} onClose={() => setDeleteOpen(false)} onDeleted={() => router.push("/")} />
        </div>
      </Stagger>
    );
  }

  const viewer = (
    <PageViewer caseId={c.id} files={c.files} documents={c.documents} flags={numbered.all} selectedFlagId={flagId} onSelectFlag={(id) => select(id, undefined)} current={loc} onNavigate={setLoc} />
  );
  const findingsPanel = (
    <div className="space-y-3">
      <FindingsPanel
        documents={c.documents}
        files={c.files}
        caseId={c.id}
        findings={c.findings}
        numbered={numbered}
        selectedFlagId={flagId}
        onSelectFlag={select}
        onShowDocument={showDocument}
        onDecide={setDecide}
        canDecide={canFlagDecide}
        partial={c.partial || !!c.score?.partial}
        partialReasons={c.partial_reasons}
        coverage={c.coverage ?? null}
        explainCase={c}
      />
      <CoveragePanel c={c} defaultOpen={c.partial || !!c.score?.partial || (c.coverage?.ratio ?? 1) < 0.7} />
      {uncheckedPageList(c).length > 0 ? (
        <UncheckedPagesPanel c={c} onView={showPage} />
      ) : (
        <NotCheckedList items={c.not_checked_documents} onView={showPage} documents={c.documents} files={c.files} explainCase={c} />
      )}
    </div>
  );
  const panels: Record<string, React.ReactNode> = {
    findings: findingsPanel,
    page: (
      <div className="space-y-3">
        {viewer}
        {selectedNf ? (
          <div className="rounded-lg border border-brand-2 bg-brand-tint p-3 text-sm">
            <p className="font-semibold">
              Flag {selectedNf.n}: <code className="num text-xs">{selectedNf.flag.rule}</code>
            </p>
            <p className="mt-1">{selectedNf.flag.explanation}</p>
            <Button className="mt-2" variant="secondary" onClick={() => goTab("findings")}>
              See in findings
            </Button>
          </div>
        ) : null}
      </div>
    ),
    documents: <DocumentsPanel c={c} onShowDocument={showDocument} onShowPage={showPage} />,
    graph: graph ? <EntityGraph graph={graph} onSelectEdge={selectEdge} /> : null,
    review: (
      <ReviewPanel
        c={c}
        isCurrent={isCurrent}
        onDone={afterReview}
        onSelectFlag={(id) => {
          goTab("findings");
          select(id, undefined);
        }}
        onReload={() => void reload()}
        onDocsStarted={(r) => startedNewVersion(r.job_id)}
        onRerun={() => setRerunOpen(true)}
        onRecord={() => setDecisionOpen(true)}
      />
    ),
  };

  return (
    <>
      <a href="#findings-region" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:shadow-lift">
        Skip to findings
      </a>
      <Stagger className="space-y-6">
        <CaseHeader
          c={c}
          isCurrent={isCurrent}
          versions={versions}
          onVersion={(v) => setParams({ v: versions.find((x) => x.version === v)?.current ? null : String(v), flag: null })}
          nav={nav}
        />
        <div className="space-y-3 empty:hidden">
          <CaseBanners
            c={c}
            isCurrent={isCurrent}
            jobError={null}
            onGoCoverage={() => {
              goTab("findings");
              document.getElementById("coverage")?.scrollIntoView?.({ block: "start" });
            }}
            onGoReview={() => goTab("review")}
            onRerun={() => setRerunOpen(true)}
            currentHref={currentHref}
          />
          {recorded ? (
            <Notice variant="success" title="Decision recorded" onDismiss={() => setRecorded(null)}>
              {recorded}
            </Notice>
          ) : null}
          {actionError ? <CaseErrorNotice error={actionError} /> : null}
        </div>

        <RecommendationHero
          c={c}
          isCurrent={isCurrent}
          why={why}
          onRecord={() => setDecisionOpen(true)}
          onShowFindings={goFindings}
          onShowUnreviewed={goUnreviewed}
          onOpenActivity={() => goTab("review")}
          onRerun={() => setRerunOpen(true)}
          onAddDocs={() => setDocsOpen(true)}
          onOpenTrail={() => setTrailOpen(true)}
          actions={<CaseActions c={c} isCurrent={isCurrent} onRerun={() => setRerunOpen(true)} onAddDocs={() => setDocsOpen(true)} onDelete={() => setDeleteOpen(true)} />}
        />

        <div className="grid gap-6 lg:grid-cols-[minmax(0,58fr)_minmax(0,42fr)]">
          {isLg ? <div className="min-w-0 self-start lg:sticky lg:top-4">{viewer}</div> : null}
          <div id="findings-region" className="min-w-0 scroll-mt-4">
            <div className="flex items-end justify-between gap-2">
              <Tabs group={GROUP} label="Case sections" tabs={tabs} value={tab} onValueChange={goTab} className="min-w-0 flex-1" />
              <button
                type="button"
                onClick={() => setHelpOpen(true)}
                className="mb-0.5 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-ink-2 hover:bg-brand-tint hover:text-brand"
                aria-label="Keyboard help"
              >
                <Keyboard aria-hidden className="h-5 w-5" />
              </button>
            </div>
            {tabs.map((t) => (
              <TabPanel key={t.id} group={GROUP} id={t.id} value={tab} keepMounted={false} className="pt-4 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-2">
                {panels[t.id]}
              </TabPanel>
            ))}
          </div>
        </div>
      </Stagger>

      {canDecide && c.status === "open" ? (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-rule bg-surface px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 shadow-lift sm:hidden">
          <Button size="lg" fullWidth icon={<Stamp aria-hidden className="h-4 w-4" />} onClick={() => setDecisionOpen(true)}>
            Record decision
          </Button>
        </div>
      ) : null}

      <DecisionSheet
        open={decisionOpen}
        onClose={() => setDecisionOpen(false)}
        c={c}
        isCurrent={isCurrent}
        onDone={afterReview}
        onSelectFlag={(id) => {
          goTab("findings");
          select(id, undefined);
        }}
        onReload={() => void reload()}
      />
      <AnalysisTrailSheet
        open={trailOpen}
        onClose={() => setTrailOpen(false)}
        c={c}
        onGoCoverage={() => {
          goTab("findings");
          setTimeout(() => document.getElementById("coverage")?.scrollIntoView?.({ block: "start" }), 0);
        }}
      />
      <FlagDecisionDialog c={c} target={decide} onClose={() => setDecide(null)} onDone={afterReview} onSelectFlag={(id) => select(id)} onReload={() => void reload()} />
      <RerunDialog open={rerunOpen} nextVersion={(latest ?? c.version) + 1} onClose={() => setRerunOpen(false)} onConfirm={() => void doRerun()} busy={busy} />
      <Modal open={docsOpen} onClose={() => setDocsOpen(false)} title="Add documents">
        <AddDocuments caseId={c.id} onStarted={(r) => startedNewVersion(r.job_id)} />
      </Modal>
      <DeleteDialog open={deleteOpen} c={c} onClose={() => setDeleteOpen(false)} onDeleted={() => router.push("/")} />
      <Modal open={helpOpen} onClose={() => setHelpOpen(false)} title="Keyboard help">
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-3 text-[0.9375rem]">
          {[
            ["j / k", "Next / previous flag"],
            ["[ / ]", "Previous / next page"],
            ["Esc", "Clear the selected flag"],
          ].map(([k, d]) => (
            <div key={k} className="contents">
              <dt className="flex gap-1">
                {k.split(" / ").map((key) => (
                  <Kbd key={key}>{key}</Kbd>
                ))}
              </dt>
              <dd>{d}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-sm text-muted">Shortcuts are off while you type in a field or a dialog is open.</p>
      </Modal>
    </>
  );
}

function CaseView() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const v = search.get("v");
  const version = v && /^\d+$/.test(v) ? Number(v) : undefined;
  const jobId = search.get("job");
  const { data, error, isLoading, mutate } = useSWR(["case", id, version], () => getCase(id, version), {
    refreshInterval: (c) => (c?.status === "processing" ? 2000 : 0),
  });
  const versionsSwr = useSWR(["versions", id], () => getVersions(id).then((r) => r.versions));
  const nav = useCaseNeighbours(id);

  if (isLoading && !data) return <CaseSkeleton />;
  if (error instanceof ApiErrorException && error.error.code === "not_found") notFound();
  if (error && !data) return <CaseLoadError error={error} onRetry={() => void mutate()} />;
  if (!data) return null;
  const versions = versionsSwr.data ?? [];
  const latest = versions.find((x) => x.current)?.version;
  return (
    <CaseBody
      key={`${data.id}:${data.version}:${data.status === "processing" ? "p" : "r"}`}
      c={data}
      versionParam={version}
      jobId={jobId}
      reload={() => Promise.all([mutate(), versionsSwr.mutate()])}
      reloadVersions={() => versionsSwr.mutate()}
      versions={versions}
      latest={latest}
      nav={nav}
    />
  );
}

export default function CasePage() {
  return (
    <div className="pb-24 sm:pb-0">
      <Suspense fallback={<CaseSkeleton />}>
        <CaseView />
      </Suspense>
    </div>
  );
}
