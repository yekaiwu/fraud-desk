import type { AgentName, AgentReport } from "@/lib/types";
import { secondsText } from "@/lib/explain";
import { Badge } from "../ui/Badge";

const TITLE: Record<AgentName, string> = {
  documents: "Documents specialist",
  finance: "Finance specialist",
  identity: "Identity specialist",
  relationships: "Relationships specialist",
  challenger: "Challenger",
  case_officer: "Case officer",
};

const GENERATED = "Generated text, shown as is; it never changes the score.";

function Generated({ label, text }: { label: string; text: string }) {
  return (
    <figure className="rounded-md border border-dashed border-rule-strong bg-paper/70 px-3 py-2">
      <figcaption className="label-caps">{label}</figcaption>
      <blockquote className="mt-1 whitespace-pre-wrap break-words text-ink">{text}</blockquote>
      <p className="mt-1 text-xs text-muted">{GENERATED}</p>
    </figure>
  );
}

function Member({ r, onShowFlag }: { r: AgentReport; onShowFlag?: (flagId: string) => void }) {
  const model = r.agent === "challenger" || r.agent === "case_officer";
  const lowered = r.verdicts.filter((v) => v.action === "lowered");
  const flagLabel = (id: string) => (onShowFlag ? (
    <button type="button" onClick={() => onShowFlag(id)} className="num break-all text-left text-xs text-brand-2 underline underline-offset-2">
      {id}
    </button>
  ) : (
    <span className="num text-xs">{id}</span>
  ));
  return (
    <li className="space-y-1.5 rounded-lg border border-rule bg-surface px-3.5 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-ink">{TITLE[r.agent]}</p>
        {r.status === "failed" ? (
          <Badge variant="medium">Could not run</Badge>
        ) : r.status === "skipped" ? (
          <Badge variant="neutral" icon={null}>Nothing to do</Badge>
        ) : model ? (
          <Badge variant="neutral" icon={null}>Model call</Badge>
        ) : null}
      </div>
      <p className="text-sm text-muted">{r.role}</p>
      {r.agent === "case_officer" && r.status === "ok" ? (
        <Generated label="Summary for the reviewer" text={r.summary} />
      ) : (
        <p className="text-sm">{r.summary}</p>
      )}
      {r.next_steps.length > 0 ? (
        <div>
          <p className="label-caps">Suggested next steps</p>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-5 text-sm">
            {r.next_steps.map((s, i) => (
              <li key={i} className="break-words">
                {s}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {r.flag_ids.length > 0 && r.agent !== "case_officer" && r.agent !== "challenger" ? (
        <div>
          <p className="label-caps">Flags it passed on ({r.flag_ids.length})</p>
          <ul className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1">
            {r.flag_ids.map((id) => (
              <li key={id}>{flagLabel(id)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {r.verdicts.length > 0 ? (
        <div>
          <p className="label-caps">
            Its rulings ({r.verdicts.length}): {r.verdicts.length - lowered.length} kept, {lowered.length} lowered
          </p>
          <ul className="mt-0.5 space-y-1.5 text-sm">
            {r.verdicts.map((v) => (
              <li key={v.flag_id} className="break-words rounded-md border border-rule bg-paper/60 px-2.5 py-1.5">
                {flagLabel(v.flag_id)}{" "}
                <Badge variant={v.action === "lowered" ? "medium" : "neutral"} icon={null}>
                  {v.verdict === "upheld" ? "Upheld" : "Doubtful"}, {v.action === "lowered" ? "lowered to info" : "kept"}
                </Badge>
                <span className="block text-ink-2">{v.reason}</span>
                {v.quote ? (
                  <span className="block">
                    Quote: <q className="text-ink">{v.quote}</q>
                    {v.document_id ? ` (${v.document_id})` : ""}
                  </span>
                ) : (
                  <span className="block text-xs text-muted">No passage quoted, so the flag stands.</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {r.handoff ? <p className="text-sm text-muted">Hand-off: {r.handoff}</p> : null}
      {model && r.status !== "skipped" ? (
        <p className="tabular text-xs text-muted">
          {secondsText(r.seconds)}, ${Number(r.cost_usd).toFixed(4)}
        </p>
      ) : null}
    </li>
  );
}

/** The six members and what each handed on, without a heading: the timeline puts them inside its own step. */
export function TeamMembers({ agents, onShowFlag }: { agents: AgentReport[]; onShowFlag?: (flagId: string) => void }) {
  return (
    <ul className="space-y-2">
      {agents.map((r) => (
        <Member key={r.agent} r={r} onShowFlag={onShowFlag} />
      ))}
    </ul>
  );
}

/** The trail of the review team: who looked at what, what was handed on, and what changed. */
export function ReviewTeam({ agents }: { agents: AgentReport[] }) {
  if (agents.length === 0) return null;
  return (
    <section aria-labelledby="trail-team" className="mt-6 space-y-3">
      <h3 id="trail-team" className="text-[1.1875rem] leading-snug">
        The review team
      </h3>
      <p className="text-sm text-ink-2">
        Four specialists group the checks by what they examine. The challenger then argues the innocent side of each judgement flag, and the case officer briefs you. Only the challenger can
        change a flag, and only by quoting the documents.
      </p>
      <ul className="space-y-2">
        {agents.map((r) => (
          <Member key={r.agent} r={r} />
        ))}
      </ul>
    </section>
  );
}
