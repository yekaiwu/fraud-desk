"use client";

import { Eye, Globe, Lock, Pencil, Power, Trash2 } from "lucide-react";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { listCustomRules, publishCustomRule, retireCustomRule, setCustomRuleEnabled, unadoptCustomRule, unpublishCustomRule } from "@/lib/api";
import { TEMPLATE_LABEL } from "@/lib/customRules";
import { formatUtc } from "@/lib/format";
import type { AdoptedRuleView, CustomRuleView } from "@/lib/types";
import { ErrorState } from "../StateViews";
import { Badge, ConcernBadge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";
import { CustomBadge } from "./CustomBadge";
import { CustomError } from "./CustomError";
import { ReasonDialog } from "./ReasonDialog";
import { RuleDetailSheet } from "./RuleDetailSheet";

export const CUSTOM_RULES_KEY = "custom-rules";
export const SHARED_RULES_KEY = "custom-rules-shared";

type Action = { kind: "enable" | "disable" | "publish" | "unpublish" | "retire"; rule: CustomRuleView };

const DIALOG: Record<Action["kind"], { title: string; confirm: string; intro: (r: CustomRuleView) => React.ReactNode; danger?: boolean }> = {
  enable: { title: "Switch this rule on", confirm: "Switch on", intro: () => "It runs again on analyses you start from now on." },
  disable: { title: "Switch this rule off", confirm: "Switch off", intro: () => "It stops running on analyses you start from now on. Past results keep showing what ran." },
  publish: {
    title: "Make this rule public",
    confirm: "Make public",
    intro: (r) => (
      <>
        Every signed-in account will see <span className="font-medium text-ink">{r.name}</span> with your name on it, labelled custom and unreviewed. Nothing changes for anyone until they adopt it. Version {r.version} is frozen once it is public: to change it you save a new version.
      </>
    ),
  },
  unpublish: { title: "Take this rule back", confirm: "Take back", intro: () => "It is private again. Accounts that adopted it stop running it from their next analysis." },
  retire: { title: "Delete this rule", confirm: "Delete rule", danger: true, intro: () => "It stops running and disappears from the lists. Cases it ran on stay explainable: its name, version and wording are kept on them." },
};

function Meta({ r }: { r: CustomRuleView }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-2">
      <span className="tabular">Version {r.version}</span>
      <span aria-hidden>·</span>
      <span>{TEMPLATE_LABEL[r.template]}</span>
      <span aria-hidden>·</span>
      <span>
        saved <time dateTime={r.updated_at}>{formatUtc(r.updated_at)}</time>
      </span>
      {r.visibility === "public" ? (
        <>
          <span aria-hidden>·</span>
          <span className="tabular">{r.adoptions === 1 ? "1 account uses it" : `${r.adoptions} accounts use it`}</span>
        </>
      ) : null}
    </p>
  );
}

function RuleCard({ r, onAction, onEdit, onOpen }: { r: CustomRuleView; onAction: (a: Action) => void; onEdit: (r: CustomRuleView) => void; onOpen: (id: string) => void }) {
  const retired = r.status === "retired";
  const newerPrivate = r.visibility === "public" && r.published_version !== null && r.published_version < r.version;
  return (
    <li className={`rounded-lg border border-rule p-4 shadow-paper ${retired ? "bg-paper-2/50" : "bg-surface"}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="min-w-0 break-words text-[1.0625rem] font-semibold text-ink">{r.name}</h3>
            <CustomBadge info={{ author: r.owner, version: r.version }} />
            <ConcernBadge severity={r.severity} />
            {retired ? (
              <Badge variant="neutral" icon={null}>
                Deleted
              </Badge>
            ) : (
              <Badge variant={r.enabled ? "low" : "status"} icon={null}>
                {r.enabled ? "On" : "Switched off"}
              </Badge>
            )}
            {r.visibility === "public" ? (
              <Badge variant="lane" icon={Globe}>
                Public (v{r.published_version})
              </Badge>
            ) : (
              <Badge variant="neutral" icon={Lock}>
                Private
              </Badge>
            )}
          </div>
          <p className="mt-1.5 break-words text-[0.9375rem] text-ink">{r.readback}</p>
          <div className="mt-1">
            <Meta r={r} />
          </div>
          {newerPrivate ? <p className="mt-1 text-sm text-brass-deep">Version {r.version} is private. Others still get version {r.published_version} until you make it public.</p> : null}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-rule pt-3">
        <Button size="sm" variant="ghost" icon={<Eye aria-hidden className="h-4 w-4" />} onClick={() => onOpen(r.rule_id)}>
          Details{" "}<span className="sr-only">of {r.name}</span>
        </Button>
        {retired ? null : (
          <>
            <Button size="sm" variant="secondary" icon={<Pencil aria-hidden className="h-4 w-4" />} onClick={() => onEdit(r)}>
              New version{" "}<span className="sr-only">of {r.name}</span>
            </Button>
            <Button size="sm" variant="secondary" icon={<Power aria-hidden className="h-4 w-4" />} onClick={() => onAction({ kind: r.enabled ? "disable" : "enable", rule: r })}>
              {r.enabled ? "Switch off" : "Switch on"}
              {" "}<span className="sr-only">{r.name}</span>
            </Button>
            {r.visibility === "public" ? (
              <>
                {newerPrivate ? (
                  <Button size="sm" variant="secondary" onClick={() => onAction({ kind: "publish", rule: r })}>
                    Make version {r.version} public{" "}<span className="sr-only">({r.name})</span>
                  </Button>
                ) : null}
                <Button size="sm" variant="secondary" onClick={() => onAction({ kind: "unpublish", rule: r })}>
                  Take back{" "}<span className="sr-only">{r.name}</span>
                </Button>
              </>
            ) : (
              <Button size="sm" variant="secondary" icon={<Globe aria-hidden className="h-4 w-4" />} onClick={() => onAction({ kind: "publish", rule: r })}>
                Make public{" "}<span className="sr-only">{r.name}</span>
              </Button>
            )}
            <Button size="sm" variant="ghost" className="sm:ml-auto" icon={<Trash2 aria-hidden className="h-4 w-4" />} onClick={() => onAction({ kind: "retire", rule: r })}>
              Delete{" "}<span className="sr-only">{r.name}</span>
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

function AdoptedList({ rules, onStop, error }: { rules: AdoptedRuleView[]; onStop: (r: AdoptedRuleView) => void; error: unknown }) {
  return (
    <section aria-labelledby="adopted-title" className="space-y-2.5">
      <h2 id="adopted-title" className="text-[1.0625rem] font-semibold text-ink">
        Public checks you use
      </h2>
      {error ? <CustomError error={error} /> : null}
      {rules.length === 0 ? (
        <p className="text-[0.9375rem] text-ink-2">You have not adopted any public check. Find them under Public checks.</p>
      ) : (
        <ul className="space-y-2.5">
          {rules.map((a) => (
            <li key={a.rule_id} className="rounded-lg border border-rule bg-surface p-4 shadow-paper">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="min-w-0 break-words text-[1rem] font-semibold text-ink">{a.name}</h3>
                <CustomBadge info={{ author: a.author, version: a.version }} />
                {a.severity ? <ConcernBadge severity={a.severity} /> : null}
                {a.active ? null : (
                  <Badge variant="status" icon={null}>
                    Not running: no longer public
                  </Badge>
                )}
              </div>
              {a.readback ? <p className="mt-1.5 break-words text-[0.9375rem] text-ink">{a.readback}</p> : null}
              <p className="mt-1 text-sm text-ink-2">
                By {a.author}, version {a.version} adopted. {a.update_available ? `Version ${a.latest_version} is public: adopt it again under Public checks to use it.` : ""}
              </p>
              <div className="mt-3 border-t border-rule pt-3">
                <Button size="sm" variant="secondary" onClick={() => onStop(a)}>
                  Stop using{" "}<span className="sr-only">{a.name}</span>
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** My checks: the rules this account wrote (switch on or off, make public, new version, delete) and the public ones it adopted. */
export function MyCustomRules({ onEdit, onNew }: { onEdit: (r: { id: string; name: string }) => void; onNew: () => void }) {
  const { data, error, isLoading, mutate } = useSWR(CUSTOM_RULES_KEY, listCustomRules);
  const { mutate: refresh } = useSWRConfig();
  const [action, setAction] = useState<Action | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [stopError, setStopError] = useState<unknown>(null);

  async function confirm(reason: string) {
    if (!action) return;
    const { kind, rule } = action;
    if (kind === "enable" || kind === "disable") await setCustomRuleEnabled(rule.rule_id, kind === "enable", reason);
    else if (kind === "publish") await publishCustomRule(rule.rule_id, reason);
    else if (kind === "unpublish") await unpublishCustomRule(rule.rule_id, reason);
    else await retireCustomRule(rule.rule_id, reason);
    setDone(
      {
        enable: `${rule.name} is on.`,
        disable: `${rule.name} is switched off.`,
        publish: `${rule.name} is public. Other accounts can adopt it.`,
        unpublish: `${rule.name} is private again.`,
        retire: `${rule.name} was deleted.`,
      }[kind],
    );
    setAction(null);
    await mutate();
    await refresh(SHARED_RULES_KEY);
  }

  async function stop(a: AdoptedRuleView) {
    setStopError(null);
    try {
      await unadoptCustomRule(a.rule_id);
      setDone(`You stopped using ${a.name}.`);
      await mutate();
      await refresh(SHARED_RULES_KEY);
    } catch (e) {
      setStopError(e);
    }
  }

  if (isLoading && !data) {
    return (
      <div role="status" aria-busy="true" className="space-y-3">
        <span className="sr-only">Loading your rules...</span>
        <Skeleton className="h-28 rounded-lg" />
        <Skeleton className="h-28 rounded-lg" />
      </div>
    );
  }
  if (error && !data) return <ErrorState error={error} onRetry={() => void mutate()} />;
  const mine = (data?.mine ?? []).filter((r) => r.status === "active");
  const deleted = (data?.mine ?? []).filter((r) => r.status === "retired");
  const cfg = action ? DIALOG[action.kind] : null;

  return (
    <div className="space-y-6">
      {done ? (
        <Notice variant="success" title={done} onDismiss={() => setDone(null)} />
      ) : null}
      <section aria-labelledby="mine-title" className="space-y-2.5">
        <h2 id="mine-title" className="text-[1.0625rem] font-semibold text-ink">
          Checks you wrote
        </h2>
        {mine.length === 0 ? (
          <EmptyState
            title="You have not written a custom check yet"
            text="Describe a check in plain English and the assistant turns it into a check that only you use until you make it public."
            action={<Button onClick={onNew}>Create a custom check</Button>}
          />
        ) : (
          <ul className="space-y-3">
            {mine.map((r) => (
              <RuleCard key={r.rule_id} r={r} onAction={setAction} onEdit={(x) => onEdit({ id: x.rule_id, name: x.name })} onOpen={setDetail} />
            ))}
          </ul>
        )}
      </section>

      <AdoptedList rules={data?.adopted ?? []} onStop={(a) => void stop(a)} error={stopError} />

      {deleted.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-sm font-medium text-brand-2 underline underline-offset-2">Deleted rules ({deleted.length})</summary>
          <ul className="mt-2 space-y-3">
            {deleted.map((r) => (
              <RuleCard key={r.rule_id} r={r} onAction={setAction} onEdit={() => {}} onOpen={setDetail} />
            ))}
          </ul>
        </details>
      ) : null}

      <ReasonDialog
        key={action ? `${action.kind}-${action.rule.rule_id}` : "closed"}
        open={action !== null}
        title={cfg?.title ?? ""}
        intro={action && cfg ? cfg.intro(action.rule) : null}
        confirmLabel={cfg?.confirm ?? ""}
        danger={cfg?.danger}
        onClose={() => setAction(null)}
        onConfirm={confirm}
      />
      <RuleDetailSheet ruleId={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
