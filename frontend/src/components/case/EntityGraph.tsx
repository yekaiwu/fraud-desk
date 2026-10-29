"use client";

import clsx from "clsx";
import { Info, Network, Table2 } from "lucide-react";
import { useId, useState } from "react";
import { fmtCents } from "@/lib/decimal";
import { isInAndOut, type EntityGraph as Graph, type GEdge, type GNode } from "@/lib/graph";
import { useIsSm } from "@/lib/useMedia";
import { EmptyState } from "../ui";

const W = 1000;
const H = 600;
const NODE_W = 188;
const NODE_H = 64;

const KIND_TEXT: Record<GEdge["kind"], string> = { owns: "owns", director: "director of", paid: "paid", paid_out: "paid out to", invoiced: "invoiced" };

/** Word-wraps a name onto at most two lines of about `n` characters; the full name stays in the tooltip. */
function wrapLabel(s: string, n = 22): string[] {
  const clip = (t: string) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);
  if (s.length <= n) return [s];
  const words = s.split(/\s+/);
  let line1 = "";
  let i = 0;
  while (i < words.length && (line1 ? line1.length + 1 + words[i].length : words[i].length) <= n) {
    line1 = line1 ? `${line1} ${words[i]}` : words[i];
    i++;
  }
  if (!line1) {
    const rest = words.slice(1).join(" ");
    return rest ? [clip(words[0]), clip(rest)] : [clip(words[0])];
  }
  const rest = words.slice(i).join(" ");
  return rest ? [line1, clip(rest)] : [line1];
}

/** Rows are spaced 68 units apart so boxes never overlap; a crowded side makes the drawing taller, not denser. */
function layout(g: Graph): { pos: Map<string, { x: number; y: number }>; height: number } {
  const people = g.nodes.filter((n) => n.id !== "applicant" && n.kind !== "counterparty");
  const left = g.nodes.filter((n) => n.kind === "counterparty" && (n.roles.includes("payer") || n.roles.includes("supplier")));
  const right = g.nodes.filter((n) => n.kind === "counterparty" && !left.includes(n));
  const side = Math.max(left.length, right.length, 1);
  const height = Math.max(H, side * 68 + 190);
  const pos = new Map<string, { x: number; y: number }>([["applicant", { x: 500, y: height / 2 + 30 }]]);
  const top = 170;
  const bottom = height - 60;
  const spread = (list: GNode[], x: number) =>
    list.forEach((n, i) => pos.set(n.id, { x, y: list.length === 1 ? (top + bottom) / 2 : top + (i * (bottom - top)) / (list.length - 1) }));
  spread(left, 120);
  spread(right, 880);
  people.forEach((n, i) => pos.set(n.id, { x: people.length === 1 ? 500 : 150 + (i * 700) / (people.length - 1), y: 92 }));
  return { pos, height };
}

function strokeWidth(e: GEdge): number {
  if (e.amountCents === undefined) return 2;
  const digits = (e.amountCents < 0n ? -e.amountCents : e.amountCents) / 100n;
  return 1.5 + Math.min(4.5, digits.toString().length / 1.5);
}

const edgeColor = (e: GEdge) => (e.flagged ? "var(--sev-high)" : e.kind === "owns" || e.kind === "director" ? "var(--brand-2)" : e.kind === "invoiced" ? "var(--muted)" : "var(--ink-2)");

function Legend({ hasFlagged, hasInOut, hasCompany }: { hasFlagged: boolean; hasInOut: boolean; hasCompany: boolean }) {
  const swatch = "inline-block h-3.5 w-7 shrink-0";
  return (
    <ul aria-label="Key to the graph" className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-ink-2">
      <li className="inline-flex items-center gap-2">
        <span aria-hidden className={clsx(swatch, "rounded-full bg-brand")} />
        The applicant
      </li>
      <li className="inline-flex items-center gap-2">
        <span aria-hidden className={clsx(swatch, "rounded-[4px] border-[1.5px] border-rule-strong bg-surface")} />
        A person or counterparty
      </li>
      {hasCompany ? (
        <li className="inline-flex items-center gap-2">
          <span aria-hidden className={clsx(swatch, "rounded-[4px] border-[1.5px] border-dashed border-ink-2 bg-surface")} />
          A company (dashed outline)
        </li>
      ) : null}
      <li className="inline-flex items-center gap-2">
        <span aria-hidden className="inline-block h-0.5 w-7 shrink-0 bg-brand-2" />
        Ownership or directorship
      </li>
      <li className="inline-flex items-center gap-2">
        <span aria-hidden className="inline-block h-1 w-7 shrink-0 bg-ink-2" />
        Payments (thicker means more money)
      </li>
      {hasFlagged ? (
        <li className="inline-flex items-center gap-2">
          <span aria-hidden className="inline-block h-0 w-7 shrink-0 border-t-[3px] border-dashed border-sev-high" />
          Linked to a concern
        </li>
      ) : null}
      {hasInOut ? <li className="inline-flex items-center gap-2 font-medium text-sev-medium">In and out: both paid the applicant and was paid by it</li> : null}
    </ul>
  );
}

export function EntityGraph({ graph, onSelectEdge }: { graph: Graph; onSelectEdge: (e: GEdge) => void }) {
  const isSm = useIsSm();
  const [pick, setPick] = useState<"graph" | "table" | null>(null);
  const view = pick ?? (isSm ? "graph" : "table");
  const tableId = useId();
  const flagged = graph.edges.filter((e) => e.flagged).length;
  const { pos, height: svgH } = layout(graph);
  const label = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? id;
  const parties = graph.nodes.length;
  const hasLeft = graph.nodes.some((n) => n.kind === "counterparty" && (n.roles.includes("payer") || n.roles.includes("supplier")));
  const hasRight = graph.nodes.some((n) => n.kind === "counterparty" && !(n.roles.includes("payer") || n.roles.includes("supplier")));

  return (
    <section aria-label="Entity graph" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="flex items-start gap-2 text-sm text-muted">
          <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
          Names matched exactly after normalising; the backend matcher may differ.
        </p>
        <div role="group" aria-label="Graph view" className="flex overflow-hidden rounded-md border border-rule-strong bg-surface">
          {(["graph", "table"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setPick(v)}
              className={clsx("inline-flex min-h-9 items-center gap-1.5 px-3 text-sm font-semibold pointer-coarse:min-h-11", view === v ? "bg-brand text-on-brand" : "text-brand hover:bg-brand-tint")}
            >
              {v === "graph" ? <Network aria-hidden className="h-4 w-4" /> : <Table2 aria-hidden className="h-4 w-4" />}
              {v === "graph" ? "Graph" : "Table"}
            </button>
          ))}
        </div>
      </div>

      {graph.edges.length === 0 ? <EmptyState compact icon={<Network aria-hidden className="h-6 w-6" />} title="Nothing to draw" text="No counterparties, owners or directors were extracted from the documents." /> : null}

      {view === "graph" && graph.edges.length > 0 ? (
        <figure className="space-y-3">
          <svg
            viewBox={`0 0 ${W} ${svgH}`}
            style={{ width: "100%", fontFamily: "var(--font-sans)" }}
            role="img"
            aria-label={`Entity graph, ${parties} parties, ${graph.edges.length} relationships, ${flagged} flagged`}
            aria-describedby={tableId}
            className="rounded-lg border border-rule bg-surface shadow-paper"
          >
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
              </marker>
            </defs>
            {hasLeft ? (
              <text x={120} y={36} textAnchor="middle" fontSize="13" fontWeight={600} letterSpacing="1.2" style={{ fill: "var(--muted)" }}>
                MONEY IN
              </text>
            ) : null}
            {hasRight ? (
              <text x={880} y={36} textAnchor="middle" fontSize="13" fontWeight={600} letterSpacing="1.2" style={{ fill: "var(--muted)" }}>
                MONEY OUT
              </text>
            ) : null}
            {graph.edges.map((e) => {
              const a = pos.get(e.source);
              const b = pos.get(e.target);
              if (!a || !b) return null;
              const mx = (a.x + b.x) / 2;
              const my = (a.y + b.y) / 2;
              const dx = b.x - a.x;
              const dy = b.y - a.y;
              const len = Math.hypot(dx, dy) || 1;
              const bend = e.kind === "paid" ? 30 : e.kind === "paid_out" ? -30 : 0;
              const cx = mx + (-dy / len) * bend;
              const cy = my + (dx / len) * bend;
              const lx = (a.x + 2 * cx + b.x) / 4;
              const ly = (a.y + 2 * cy + b.y) / 4;
              const money = e.kind === "paid" || e.kind === "paid_out" || e.kind === "invoiced";
              return (
                <g key={e.id} onClick={() => onSelectEdge(e)} style={{ cursor: e.rows.length ? "pointer" : "default" }}>
                  <path
                    d={`M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`}
                    fill="none"
                    style={{ stroke: edgeColor(e) }}
                    strokeWidth={strokeWidth(e)}
                    strokeDasharray={e.flagged ? "9 6" : undefined}
                    markerEnd="url(#arrow)"
                    opacity={0.8}
                  />
                  <text
                    x={lx}
                    y={ly - 7}
                    textAnchor="middle"
                    fontSize="16"
                    fontWeight={e.flagged ? 700 : 500}
                    paintOrder="stroke"
                    strokeWidth="5"
                    strokeLinejoin="round"
                    style={{ fill: e.flagged ? "var(--sev-high)" : "var(--ink)", stroke: "var(--surface)", fontFamily: money ? "var(--font-mono)" : undefined }}
                  >
                    {e.label}
                    {e.flagged ? " (flagged)" : ""}
                  </text>
                </g>
              );
            })}
            {graph.nodes.map((n) => {
              const p = pos.get(n.id);
              if (!p) return null;
              const app = n.kind === "applicant";
              const lines = wrapLabel(n.label);
              const tags: { text: string; color: string }[] = [];
              if (isInAndOut(n)) tags.push({ text: "in and out", color: "var(--sev-medium)" });
              if (n.flagged && !app) tags.push({ text: "flagged", color: "var(--sev-high)" });
              const total = lines.length + (tags.length ? 1 : 0);
              const y0 = -((total - 1) * 9);
              return (
                <g key={n.id} transform={`translate(${p.x} ${p.y})`}>
                  <title>{n.label}</title>
                  <rect
                    x={-NODE_W / 2}
                    y={-NODE_H / 2}
                    width={NODE_W}
                    height={NODE_H}
                    rx={app ? NODE_H / 2 : 8}
                    style={{ fill: app ? "var(--brand)" : "var(--surface)", stroke: n.flagged ? "var(--sev-high)" : app ? "var(--brand)" : "var(--rule-strong)" }}
                    strokeWidth={n.flagged ? 3 : 1.5}
                    strokeDasharray={n.kind === "company" ? "6 4" : undefined}
                  />
                  {lines.map((t, i) => (
                    <text key={i} textAnchor="middle" x={0} y={y0 + i * 18 + 5} fontSize="15" fontWeight={app ? 700 : 600} style={{ fill: app ? "var(--on-brand)" : "var(--ink)" }}>
                      {t}
                    </text>
                  ))}
                  {tags.length ? (
                    <text textAnchor="middle" x={0} y={y0 + lines.length * 18 + 4} fontSize="12.5" fontWeight={700}>
                      {tags.map((t, i) => (
                        <tspan key={t.text} dx={i ? 10 : 0} style={{ fill: t.color }}>
                          {t.text}
                        </tspan>
                      ))}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>
          <figcaption>
            <Legend hasFlagged={flagged > 0 || graph.nodes.some((n) => n.flagged)} hasInOut={graph.nodes.some(isInAndOut)} hasCompany={graph.nodes.some((n) => n.kind === "company")} />
          </figcaption>
        </figure>
      ) : null}

      <div id={tableId} className={clsx(view === "graph" && "sr-only")}>
        <div className="overflow-x-auto rounded-lg border border-rule bg-surface shadow-paper" role="region" aria-label="Relationships table" tabIndex={0}>
          <table className="w-full min-w-[34rem] text-left text-[0.9375rem]">
            <caption className="sr-only">Relationships between parties in this case</caption>
            <thead className="bg-paper-2 text-sm text-muted">
              <tr>
                <th scope="col" className="px-3 py-2.5 font-semibold">From</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">To</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Kind</th>
                <th scope="col" className="px-3 py-2.5 text-right font-semibold">Amount</th>
                <th scope="col" className="px-3 py-2.5 text-right font-semibold">Count</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Flagged</th>
              </tr>
            </thead>
            <tbody>
              {graph.edges.map((e) => (
                <tr key={e.id} className="border-t border-rule align-top">
                  <td className="px-3 py-2.5">{label(e.source)}</td>
                  <td className="px-3 py-2.5">{label(e.target)}</td>
                  <td className="px-3 py-2.5 text-ink-2">{KIND_TEXT[e.kind]}</td>
                  <td className="num px-3 py-2.5 text-right">{e.amountCents !== undefined ? fmtCents(e.amountCents) : e.kind === "owns" ? e.label : "-"}</td>
                  <td className="tabular px-3 py-2.5 text-right">{e.count ?? "-"}</td>
                  <td className="px-3 py-2.5">
                    {e.flagged ? (
                      <button type="button" onClick={() => onSelectEdge(e)} className="min-h-11 text-left font-semibold text-sev-high underline underline-offset-2">
                        Flagged: show on document
                      </button>
                    ) : e.rows.length ? (
                      <button type="button" onClick={() => onSelectEdge(e)} className="min-h-11 text-left text-brand-2 underline underline-offset-2">
                        Show on document
                      </button>
                    ) : (
                      <span className="text-muted">No</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="mt-2 space-y-0.5 text-sm text-muted">
          {graph.nodes.filter(isInAndOut).map((n) => (
            <li key={n.id}>{n.label}: in and out (both paid the applicant and was paid by it)</li>
          ))}
        </ul>
      </div>
      {graph.truncated > 0 ? <p className="text-sm text-muted">{graph.truncated} more parties are collapsed into “+n more”.</p> : null}
    </section>
  );
}
