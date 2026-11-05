"use client";

import { Users } from "lucide-react";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { adoptCustomRule, listSharedCustomRules, unadoptCustomRule, unpublishCustomRule } from "@/lib/api";
import { TEMPLATE_LABEL } from "@/lib/customRules";
import { formatUtc } from "@/lib/format";
import type { SharedRuleView } from "@/lib/types";
import { ErrorState } from "../StateViews";
import { Badge, ConcernBadge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";
import { CustomBadge } from "./CustomBadge";
import { CustomError } from "./CustomError";
import { CUSTOM_RULES_KEY, SHARED_RULES_KEY } from "./MyCustomRules";
import { ReasonDialog } from "./ReasonDialog";

function Card({ r, admin, busy, onAdopt, onStop, onUnpublish }: { r: SharedRuleView; admin: boolean; busy: boolean; onAdopt: (r: SharedRuleView) => void; onStop: (r: SharedRuleView) => void; onUnpublish: (r: SharedRuleView) => void }) {
  const newer = r.adopted && r.adopted_version !== null && r.adopted_version < r.version;
  return (
    <li className="rounded-lg border border-rule bg-surface p-4 shadow-paper">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 break-words text-[1.0625rem] font-semibold text-ink">{r.name}</h3>
        <CustomBadge info={{ author: r.author, version: r.version }} />
        <ConcernBadge severity={r.severity} />
        <Badge variant="neutral" icon={null}>
          {r.label}
        </Badge>
        {r.adopted ? (
          <Badge variant="lane" icon={null}>
            Adopted (v{r.adopted_version})
          </Badge>
        ) : null}
      </div>
      <p className="mt-1.5 break-words text-[0.9375rem] text-ink">{r.readback}</p>
      {r.description ? <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink-2">{r.description}</p> : null}
      <p className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-sm text-ink-2">
        <span>
          By <span className="font-medium text-ink">{r.author}</span>
          {r.own ? " (you)" : ""}
        </span>
        <span aria-hidden>·</span>
        <span className="tabular">Version {r.version}</span>
        <span aria-hidden>·</span>
        <span>{TEMPLATE_LABEL[r.template]}</span>
        <span aria-hidden>·</span>
        <span>
          public since <time dateTime={r.published_at}>{formatUtc(r.published_at)}</time>
        </span>
        <span aria-hidden>·</span>
        <span className="tabular">{r.adoptions === 1 ? "1 account uses it" : `${r.adoptions} accounts use it`}</span>
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-rule pt-3">
        {r.own ? (
          <p className="text-sm text-muted">Your own rule. Manage it under My checks.</p>
        ) : r.adopted ? (
          <>
            {newer ? (
              <Button size="sm" disabled={busy} onClick={() => onAdopt(r)}>
                Adopt version {r.version}
                {" "}<span className="sr-only">of {r.name}</span>
              </Button>
            ) : null}
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => onStop(r)}>
              Stop using{" "}<span className="sr-only">{r.name}</span>
            </Button>
            {newer ? <span className="text-sm text-brass-deep">Update available: you use version {r.adopted_version}.</span> : null}
          </>
        ) : (
          <Button size="sm" disabled={busy} onClick={() => onAdopt(r)}>
            Adopt{" "}<span className="sr-only">{r.name}</span>
          </Button>
        )}
        {admin && !r.own ? (
          <Button size="sm" variant="ghost" className="sm:ml-auto" onClick={() => onUnpublish(r)}>
            Take back (administrator){" "}<span className="sr-only">{r.name}</span>
          </Button>
        ) : null}
      </div>
    </li>
  );
}

/**
 * Public rules from every account. Nothing here changes anyone's results until they adopt a rule; an adoption is pinned
 * to the version adopted and runs on analyses that account starts. An administrator can take any public rule back.
 */
export function SharedCustomRules({ admin }: { admin: boolean }) {
  const { data, error, isLoading, mutate } = useSWR(SHARED_RULES_KEY, listSharedCustomRules);
  const { mutate: refresh } = useSWRConfig();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const [taking, setTaking] = useState<SharedRuleView | null>(null);

  async function run(r: SharedRuleView, fn: () => Promise<unknown>, message: string) {
    setBusyId(r.rule_id);
    setActionError(null);
    try {
      await fn();
      setDone(message);
      await mutate();
      await refresh(CUSTOM_RULES_KEY);
    } catch (e) {
      setActionError(e);
    } finally {
      setBusyId(null);
    }
  }

  if (isLoading && !data) {
    return (
      <div role="status" aria-busy="true" className="space-y-3">
        <span className="sr-only">Loading public rules...</span>
        <Skeleton className="h-28 rounded-lg" />
      </div>
    );
  }
  if (error && !data) return <ErrorState error={error} onRetry={() => void mutate()} />;
  const rules = data?.rules ?? [];

  return (
    <div className="space-y-4">
      <Notice variant="info" title="Written by officers, not reviewed by the development team">
        Nothing here changes your results until you adopt a rule. An adopted rule runs on analyses you start, at the version you adopted, and is labelled custom on the case.
      </Notice>
      {done ? <Notice variant="success" title={done} onDismiss={() => setDone(null)} /> : null}
      {actionError ? <CustomError error={actionError} /> : null}
      {rules.length === 0 ? (
        <EmptyState icon={<Users aria-hidden className="h-6 w-6" />} title="No public rules yet" text="When an officer makes a rule public it appears here, with their name on it." />
      ) : (
        <ul className="space-y-3">
          {rules.map((r) => (
            <Card
              key={r.rule_id}
              r={r}
              admin={admin}
              busy={busyId === r.rule_id}
              onAdopt={(x) => void run(x, () => adoptCustomRule(x.rule_id), `You adopted ${x.name} (version ${x.version}). It runs on analyses you start from now on.`)}
              onStop={(x) => void run(x, () => unadoptCustomRule(x.rule_id), `You stopped using ${x.name}.`)}
              onUnpublish={setTaking}
            />
          ))}
        </ul>
      )}
      <ReasonDialog
        key={taking?.rule_id ?? "closed"}
        open={taking !== null}
        title="Take this public rule back"
        intro={
          <>
            <span className="font-medium text-ink">{taking?.name}</span> by {taking?.author} becomes private again. Accounts that adopted it stop running it from their next analysis. The author is told in the change log.
          </>
        }
        confirmLabel="Take back"
        danger
        onClose={() => setTaking(null)}
        onConfirm={async (reason) => {
          if (!taking) return;
          await unpublishCustomRule(taking.rule_id, reason);
          setDone(`${taking.name} was taken back.`);
          setTaking(null);
          await mutate();
        }}
      />
    </div>
  );
}
