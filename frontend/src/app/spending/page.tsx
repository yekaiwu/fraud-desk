"use client";

import { Wallet } from "lucide-react";
import useSWR from "swr";
import { ErrorState } from "@/components/StateViews";
import { EmptyState, MeterBar, Panel, SectionHeading, Skeleton, Stagger, Stat } from "@/components/ui";
import { getSpending } from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";

const usd = (v: string) => `$${Number(v).toFixed(2)}`;
const usd4 = (v: string) => `$${Number(v).toFixed(4)}`;

export default function SpendingPage() {
  const { data, error, isLoading, mutate } = useSWR("spending", getSpending, { refreshInterval: 15_000 });
  const notAllowed = error instanceof ApiErrorException && error.status === 403;

  return (
    <Stagger className="space-y-6">
      <SectionHeading level={1} size="page" description="What the model has cost today, per signed-in account, as charged by the paid backend. The day runs midnight to midnight UTC.">
        Spending
      </SectionHeading>

      {isLoading && !data ? (
        <div role="status" aria-busy="true" className="space-y-4">
          <span className="sr-only">Loading spending...</span>
          <Skeleton className="h-28 rounded-lg" />
          <Skeleton className="h-64 rounded-lg" />
        </div>
      ) : null}
      {notAllowed ? (
        <EmptyState icon={<Wallet aria-hidden className="h-6 w-6" />} title="Only the administrator account can see this" text="Everybody's spending is shown to the account the site owner named as administrator." />
      ) : error && !data ? (
        <ErrorState error={error} onRetry={() => void mutate()} />
      ) : null}

      {data ? (
        <>
          <Panel padding="md">
            <div className="grid gap-6 sm:grid-cols-3">
              <Stat label="Everyone, today" value={usd(data.site_today_usd)} hint={Number(data.site_cap_usd) > 0 ? `of a ${usd(data.site_cap_usd)} daily cap` : "no site-wide cap"} />
              <Stat label="Your share" value={usd(data.accounts.find((a) => a.account === data.viewer)?.today_usd ?? "0")} hint={`signed in as ${data.viewer}`} />
              <Stat label="Not tied to an account" value={usd(data.unattributed_today_usd)} hint="started before accounts, or without sign-in" />
            </div>
            {Number(data.site_cap_usd) > 0 ? (
              <div className="mt-5">
                <MeterBar label="Site-wide daily cap" value={Number(data.site_today_usd)} max={Number(data.site_cap_usd)} valueText={`${usd(data.site_today_usd)} of ${usd(data.site_cap_usd)}`} tone={Number(data.site_today_usd) >= Number(data.site_cap_usd) * 0.8 ? "high" : "brand"} />
              </div>
            ) : null}
          </Panel>

          <Panel title="By account" eyebrow={Number(data.per_account_cap_usd) > 0 ? `Each account may spend ${usd(data.per_account_cap_usd)} a day` : "No per-account cap is set"}>
            {data.accounts.length === 0 ? (
              <p className="text-[0.9375rem] text-ink-2">Nobody has used the model yet in the last 7 days.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[28rem] text-left text-[0.9375rem]">
                  <thead>
                    <tr className="label-caps border-b border-rule">
                      <th scope="col" className="py-2 pr-4 font-medium">Account</th>
                      <th scope="col" className="py-2 pr-4 text-right font-medium">Today</th>
                      <th scope="col" className="w-1/3 py-2 pr-4 font-medium">Of the daily cap</th>
                      <th scope="col" className="py-2 text-right font-medium">Last 7 days</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-rule">
                    {data.accounts.map((a) => {
                      const cap = Number(data.per_account_cap_usd);
                      const today = Number(a.today_usd);
                      return (
                        <tr key={a.account}>
                          <th scope="row" className="py-2.5 pr-4 font-semibold text-ink">
                            {a.account}
                            {a.account === data.viewer ? <span className="ml-2 text-sm font-normal text-muted">(you)</span> : null}
                          </th>
                          <td className="tabular py-2.5 pr-4 text-right">{usd4(a.today_usd)}</td>
                          <td className="py-2.5 pr-4">
                            {cap > 0 ? (
                              <MeterBar label={`${a.account} against the daily cap`} hideLabel value={today} max={cap} valueText={`${Math.min(100, Math.round((today / cap) * 100))}%`} tone={today >= cap ? "high" : today >= cap * 0.8 ? "medium" : "brand"} />
                            ) : (
                              <span className="text-muted">-</span>
                            )}
                          </td>
                          <td className="tabular py-2.5 text-right">{usd4(a.week_usd)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel title="Site total, last 7 days">
            <ul className="divide-y divide-rule">
              {data.history.map((h) => (
                <li key={h.day} className="flex items-baseline justify-between py-2 text-[0.9375rem]">
                  <span className="text-ink-2">{h.day}</span>
                  <span className="tabular font-medium text-ink">{usd4(h.site_usd)}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </>
      ) : null}
    </Stagger>
  );
}
