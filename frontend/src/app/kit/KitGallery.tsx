"use client";

import { Download, Inbox, Plus, Search } from "lucide-react";
import { useState } from "react";
import {
  Badge,
  Button,
  ConcernBadge,
  EmptyState,
  Field,
  Kbd,
  LaneBadge,
  MeterBar,
  Notice,
  Panel,
  RiskBadge,
  RiskGauge,
  SectionHeading,
  Skeleton,
  SkeletonText,
  Stagger,
  Stat,
  StatusBadge,
  TabPanel,
  Tabs,
} from "@/components/ui";
import { CONFIRM_LABEL, DISMISS_LABEL, HARD_STOP_LABEL, LANES, UNCHECKED_PAGES_LABEL, coverageText, laneDescription, ruleIdText } from "@/lib/labels";

const SWATCHES = [
  ["paper", "bg-paper"],
  ["paper-2", "bg-paper-2"],
  ["surface", "bg-surface"],
  ["ink", "bg-ink"],
  ["ink-2", "bg-ink-2"],
  ["muted", "bg-muted"],
  ["rule", "bg-rule"],
  ["brand", "bg-brand"],
  ["brand-2", "bg-brand-2"],
  ["brand-tint", "bg-brand-tint"],
  ["brass", "bg-brass"],
  ["sev-high", "bg-sev-high"],
  ["sev-medium", "bg-sev-medium"],
  ["sev-low", "bg-sev-low"],
  ["sev-info", "bg-sev-info"],
];

export function KitGallery() {
  const [tab, setTab] = useState("one");
  const [pill, setPill] = useState("a");
  return (
    <Stagger className="space-y-8">
      <SectionHeading level={1} size="page" eyebrow="Developer reference" description="Every primitive in src/components/ui. Not available in production builds.">
        UI kit
      </SectionHeading>

      <Panel title="Palette" eyebrow="Tokens">
        <ul className="grid grid-cols-3 gap-3 sm:grid-cols-5 lg:grid-cols-8">
          {SWATCHES.map(([name, cls]) => (
            <li key={name} className="text-xs">
              <div className={`h-12 rounded-md border border-rule ${cls}`} />
              <p className="num mt-1 text-ink-2">{name}</p>
            </li>
          ))}
        </ul>
        <div className="mt-6 space-y-1">
          <p className="font-display text-5xl font-semibold tracking-tight">Fraunces display 48</p>
          <p className="font-display text-2xl font-semibold">Section serif 24: Why this case is risky</p>
          <p className="text-base">Hanken Grotesk body 16. A director appears on three unrelated companies in the last 90 days.</p>
          <p className="num text-sm">IBM Plex Mono: UEN 201912345K  S$1,284,500.00  Check: bs_running_balance</p>
        </div>
      </Panel>

      <Panel title="Risk gauge" eyebrow="Hero of the case page">
        <div className="flex flex-wrap items-end gap-10">
          <RiskGauge score={90} band="high" size="lg" />
          <RiskGauge score={45} band="medium" size="lg" />
          <RiskGauge score={12} band="low" size="lg" />
          <RiskGauge score={20} band="low" partial size="lg" />
          <RiskGauge score={72} band="high" size="sm" />
          <RiskGauge score={34} band="medium" size="sm" />
          <RiskGauge score={0} band="low" size="sm" />
        </div>
      </Panel>

      <Panel title="Buttons" eyebrow="Actions">
        <div className="flex flex-wrap items-center gap-3">
          <Button icon={<Plus aria-hidden className="h-4 w-4" />}>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button loading>Saving</Button>
          <Button disabled>Disabled</Button>
          <Button size="sm" variant="secondary" icon={<Download aria-hidden className="h-4 w-4" />}>Small</Button>
          <Button size="lg">Large</Button>
          <Button href="/upload" variant="secondary">As a link</Button>
        </div>
      </Panel>

      <Panel title="Badges" eyebrow="Never colour alone">
        <div className="flex flex-wrap gap-2">
          <RiskBadge band="high" />
          <RiskBadge band="medium" />
          <RiskBadge band="low" partial />
          <ConcernBadge severity="high" />
          <ConcernBadge severity="medium" />
          <ConcernBadge severity="low" />
          <ConcernBadge severity="info" />
          {LANES.map((l) => (
            <LaneBadge key={l} lane={l} />
          ))}
          <StatusBadge status="processing" />
          <StatusBadge status="open" />
          <StatusBadge status="awaiting_documents" />
          <StatusBadge status="closed" />
          <StatusBadge status="error" />
          <Badge variant="attention">Needs your attention</Badge>
          <Badge>Neutral</Badge>
        </div>
        <dl className="mt-4 grid gap-1 text-sm text-ink-2 sm:grid-cols-2">
          {LANES.map((l) => (
            <div key={l}>
              <dd>{laneDescription(l)}</dd>
            </div>
          ))}
        </dl>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Meters and stats" eyebrow="Coverage">
          <div className="space-y-4">
            <MeterBar label="Checks completed" value={41} max={54} valueText={coverageText(41, 54)} marker={0.7} />
            <MeterBar label="Low coverage" value={18} max={54} valueText={coverageText(18, 54)} tone="medium" marker={0.7} />
          </div>
          <div className="mt-6 grid grid-cols-3 gap-4">
            <Stat label="Waiting" value={12} hint="3 high risk" tone="high" />
            <Stat label="Decided today" value={7} tone="brand" />
            <Stat label="Median time" value="4m" hint="per case" />
          </div>
          <p className="mt-6 text-sm text-ink-2">
            {HARD_STOP_LABEL} · {UNCHECKED_PAGES_LABEL} · {DISMISS_LABEL} · {CONFIRM_LABEL} · <span className="num text-muted">{ruleIdText("bs_running_balance")}</span>
          </p>
        </Panel>
        <Panel title="Tabs" eyebrow="Arrow keys move between tabs">
          <Tabs group="kit" label="Demo tabs" value={tab} onValueChange={setTab} tabs={[{ id: "one", label: "Findings", count: 4 }, { id: "two", label: "Documents" }, { id: "three", label: "Review log" }]} />
          <TabPanel group="kit" id="one" value={tab} className="py-4 text-sm">Findings panel</TabPanel>
          <TabPanel group="kit" id="two" value={tab} className="py-4 text-sm">Documents panel</TabPanel>
          <TabPanel group="kit" id="three" value={tab} className="py-4 text-sm">Review log panel</TabPanel>
          <Tabs group="kit2" label="Pill tabs" variant="pill" value={pill} onValueChange={setPill} tabs={[{ id: "a", label: "Table" }, { id: "b", label: "Graph" }]} />
          <p className="mt-4 text-sm text-ink-2">
            Next concern <Kbd>j</Kbd> previous <Kbd>k</Kbd> clear <Kbd>Esc</Kbd>
          </p>
        </Panel>
      </div>

      <Panel title="Notices" eyebrow="Status messages">
        <div className="space-y-3">
          <Notice variant="success" title="Decision recorded">Saved to the review log.</Notice>
          <Notice variant="warning" title="Low coverage">Only 18 of 54 checks completed, so the score may be too low.</Notice>
          <Notice variant="error" title="Could not save" action={<Button size="sm" variant="secondary">Try again</Button>}>We could not reach the server.</Notice>
          <Notice variant="info" onDismiss={() => undefined}>Analysis finished a minute ago.</Notice>
        </div>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <EmptyState icon={<Inbox aria-hidden className="h-6 w-6" />} title="No cases match these filters" text="Clear a filter to see more of the worklist." action={<Button variant="secondary" icon={<Search aria-hidden className="h-4 w-4" />}>Clear filters</Button>} />
        <Panel title="Loading and form" eyebrow="Skeleton, Field">
          <div role="status" className="space-y-3">
            <span className="sr-only">Loading</span>
            <Skeleton className="h-6 w-40" />
            <SkeletonText lines={3} />
          </div>
          <Field className="mt-5" label="Reviewer name" hint="Shown in the review log." defaultValue="A. Tan" />
          <Field className="mt-4" label="Reason" error="Give a reason of at least 10 characters." defaultValue="ok" />
        </Panel>
      </div>
    </Stagger>
  );
}
