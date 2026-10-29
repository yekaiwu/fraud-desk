import { ArrowDownWideNarrow, Check, FileText, ListChecks, PenLine, ScanText, Upload, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { MeterBar } from "@/components/ui/MeterBar";
import { bandWord, coverageText } from "@/lib/labels";
import { DEMO_COVERAGE } from "./demo";
import { Section, SectionHead } from "./parts";

const CHECK_FAMILIES = [
  "Bank-statement arithmetic",
  "Invoice and tax rules",
  "Financial-statement identities",
  "Ownership and director cross-checks",
  "PDF and image forensics",
  "Consistency across documents",
] as const;

function MiniUpload() {
  const files: [string, string][] = [
    ["Application form", "4 pages"],
    ["Bank statements", "3 files"],
    ["Invoices", "6 files"],
  ];
  return (
    <div className="space-y-2">
      <Badge variant="lane" icon={null}>
        Loan application
      </Badge>
      <ul className="space-y-1.5">
        {files.map(([name, size]) => (
          <li key={name} className="flex items-center gap-2 rounded-md border border-rule bg-surface px-2.5 py-1.5 text-xs">
            <FileText aria-hidden className="h-3.5 w-3.5 shrink-0 text-brand-2" />
            <span className="min-w-0 font-medium text-ink">{name}</span>
            <span className="num ml-auto whitespace-nowrap text-muted">{size}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MiniRead() {
  return (
    <div className="space-y-2 rounded-md border border-rule bg-surface p-2.5">
      {[78, 64, 90].map((w, i) => (
        <div key={i} aria-hidden className="h-1.5 rounded-full bg-paper-2" style={{ width: `${w}%` }} />
      ))}
      <p className="flex items-baseline justify-between gap-2 rounded-sm bg-brand-tint px-2 py-1 text-xs">
        <span className="text-ink-2">Closing balance</span>
        <span className="num font-medium text-brand">16,111.75</span>
      </p>
      <p className="text-[0.6875rem] text-muted">Read from page 2, with its position kept</p>
    </div>
  );
}

function MiniCheck() {
  return (
    <div className="space-y-2">
      <ul className="space-y-1">
        {CHECK_FAMILIES.map((c) => (
          <li key={c} className="flex items-start gap-1.5 text-xs text-ink-2">
            <Check aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sev-low" />
            {c}
          </li>
        ))}
      </ul>
      <MeterBar label="Completed" value={DEMO_COVERAGE.ran} max={DEMO_COVERAGE.applicable} valueText={coverageText(DEMO_COVERAGE.ran, DEMO_COVERAGE.applicable)} />
    </div>
  );
}

function MiniRank() {
  const rows: [number, "high" | "medium" | "low", string][] = [
    [82, "high", "Orchard Quay"],
    [64, "high", "Telok Ayer"],
    [30, "medium", "Kallang Freight"],
  ];
  return (
    <ol className="space-y-1.5" aria-label="Sample worklist, riskiest first">
      {rows.map(([score, band, name], i) => (
        <li key={name} className="flex items-center gap-2 rounded-md border border-rule bg-surface px-2.5 py-1.5 text-xs">
          <span className="num w-3 text-muted">{i + 1}</span>
          <span className="min-w-0 flex-1 truncate font-medium text-ink" title={name}>{name}</span>
          <span className="font-display text-base font-semibold tabular text-ink">{score}</span>
          <Badge variant={band}>{bandWord(band)}</Badge>
        </li>
      ))}
    </ol>
  );
}

function MiniDecide() {
  return (
    <div className="space-y-2 rounded-md border border-rule bg-surface p-2.5 text-xs">
      <p className="label-caps">Reason (required)</p>
      <p className="rounded-sm border border-rule-strong bg-paper px-2 py-1.5 leading-snug text-ink-2">Deposit of 14,000.00 does not match the bank&rsquo;s own balance. Asking for the original statement.</p>
      <p className="flex items-center justify-between gap-2 border-t border-rule pt-2 text-muted">
        <span>Recorded with your name and the time</span>
      </p>
    </div>
  );
}

const STEPS: { no: string; title: string; line: string; icon: LucideIcon; mini: React.ReactNode }[] = [
  { no: "01", title: "Upload", line: "An officer opens a case and adds the company’s documents: a loan application, onboarding papers or source-of-funds evidence.", icon: Upload, mini: <MiniUpload /> },
  { no: "02", title: "Read", line: "Every page is read and each figure is kept with its place on the page. A page that cannot be read is listed, never skipped quietly.", icon: ScanText, mini: <MiniRead /> },
  { no: "03", title: "Check", line: "Fixed, repeatable rules test the numbers, the dates, the people and the files themselves.", icon: ListChecks, mini: <MiniCheck /> },
  { no: "04", title: "Score and rank", line: "Related flags are grouped into findings. A published formula turns them into a score, and the worklist puts the riskiest case first.", icon: ArrowDownWideNarrow, mini: <MiniRank /> },
  { no: "05", title: "Decide", line: "The officer reads the evidence and records a decision with a reason. Nothing is approved or rejected automatically.", icon: PenLine, mini: <MiniDecide /> },
];

export function Flow() {
  return (
    <Section id="flow" labelledBy="flow-title">
      <SectionHead
        no="02"
        label="How a case flows"
        id="flow-title"
        title="From upload to decision, in five steps."
        intro="The same path for every case. Each step leaves something you can inspect: the page, the rule, the score, the reason."
      />
      <ol className="mt-12 grid gap-x-6 gap-y-12 md:grid-cols-2 xl:grid-cols-5">
        {STEPS.map((s, i) => (
          <li key={s.no} className={`reveal flex flex-col ${i === STEPS.length - 1 ? "md:col-span-2 xl:col-span-1" : ""}`}>
            <div className="relative border-t-2 border-brand pt-4">
              <span aria-hidden className="absolute -top-[7px] left-0 h-3 w-3 rotate-45 bg-brass" />
              <div className="flex items-end justify-between">
                <span className="font-display text-[3.25rem] font-semibold leading-none tracking-tight text-brand/90">{s.no}</span>
                <s.icon aria-hidden className="mb-1 h-5 w-5 text-brand-2" />
              </div>
              <h3 className="mt-3 text-[1.375rem] leading-tight">
                <span className="sr-only">Step {Number(s.no)}: </span>
                {s.title}
              </h3>
              <p className="mt-2 text-[0.9375rem] leading-relaxed text-ink-2">{s.line}</p>
            </div>
            <div className="mt-5 flex-1 rounded-lg border border-rule bg-paper-2/70 p-3">{s.mini}</div>
          </li>
        ))}
      </ol>
    </Section>
  );
}
