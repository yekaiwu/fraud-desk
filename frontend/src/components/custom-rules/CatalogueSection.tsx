"use client";

import Link from "next/link";
import useSWR from "swr";
import { listCustomRules } from "@/lib/api";
import { TEMPLATE_LABEL } from "@/lib/customRules";
import { useMe } from "@/lib/useMe";
import { Badge, ConcernBadge } from "../ui/Badge";
import { Panel } from "../ui/Panel";
import { CustomBadge } from "./CustomBadge";
import { CUSTOM_RULES_KEY } from "./MyCustomRules";

/**
 * The viewer's own custom rules and the public ones they adopted, under the built-in catalogue, each with a Custom
 * badge. Shown only when the server has custom rules on for this account; it never lists anyone else's private rules.
 */
export function CatalogueCustomSection() {
  const me = useMe();
  const on = me?.custom_rules === true;
  const { data, error } = useSWR(on ? CUSTOM_RULES_KEY : null, listCustomRules, { revalidateOnFocus: false });
  if (!on) return null;
  const mine = (data?.mine ?? []).filter((r) => r.status === "active");
  const adopted = (data?.adopted ?? []).filter((a) => a.active); // a rule its owner took back is not running
  return (
    <Panel
      title="Custom rules"
      eyebrow="Written by officers, not reviewed by the development team"
      actions={
        <Link href="/rules/custom" className="text-sm font-medium text-brand-2 underline underline-offset-2">
          Open custom rules
        </Link>
      }
    >
      {data === undefined ? (
        <p className="text-[0.9375rem] text-ink-2">
          {error ? "Your custom rules could not be loaded just now." : "Loading your custom rules."}
        </p>
      ) : mine.length === 0 && adopted.length === 0 ? (
        <p className="text-[0.9375rem] text-ink-2">
          You have no custom rules. Describe a check in plain English under <Link href="/rules/custom" className="font-medium text-brand-2 underline underline-offset-2">Custom rules</Link>.
        </p>
      ) : (
        <ul aria-label="Your custom rules" className="divide-y divide-rule">
          {mine.map((r) => (
            <li key={r.rule_id} className="space-y-1 py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 break-words text-[0.9375rem] font-semibold text-ink">{r.name}</span>
                <CustomBadge info={{ author: r.owner, version: r.version }} />
                <ConcernBadge severity={r.severity} />
                {r.enabled ? null : (
                  <Badge variant="status" icon={null}>
                    Switched off
                  </Badge>
                )}
                {r.visibility === "public" ? (
                  <Badge variant="lane" icon={null}>
                    Public
                  </Badge>
                ) : null}
              </div>
              <p className="break-words text-sm text-ink-2">{r.readback}</p>
              <p className="num text-xs text-muted">
                {r.rule_id} <span aria-hidden>·</span> v{r.version} <span aria-hidden>·</span> {TEMPLATE_LABEL[r.template]}
              </p>
            </li>
          ))}
          {adopted.map((a) => (
            <li key={a.rule_id} className="space-y-1 py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 break-words text-[0.9375rem] font-semibold text-ink">{a.name}</span>
                <CustomBadge info={{ author: a.author, version: a.version }} />
                {a.severity ? <ConcernBadge severity={a.severity} /> : null}
                <Badge variant="neutral" icon={null}>
                  Adopted from {a.author}
                </Badge>
              </div>
              <p className="break-words text-sm text-ink-2">{a.readback}</p>
              <p className="num text-xs text-muted">
                {a.rule_id} <span aria-hidden>·</span> v{a.version}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
