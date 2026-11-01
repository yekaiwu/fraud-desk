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

function Member({ r }: { r: AgentReport }) {
  const model = r.agent === "challenger" || r.agent === "case_officer";
  const lowered = r.verdicts.filter((v) => v.action === "lowered");
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
      {lowered.length > 0 ? (
        <div>
          <p className="label-caps">Flags lowered, with the passage that explains each</p>
          <ul className="mt-0.5 space-y-1 text-sm">
            {lowered.map((v) => (
              <li key={v.flag_id} className="break-words">
                <span className="num text-xs">{v.flag_id}</span>: {v.reason}
                {v.quote ? (
                  <>
                    {" "}
                    <q className="text-ink">{v.quote}</q> ({v.document_id})
                  </>
                ) : null}
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
