"use client";

import { useEffect, useRef } from "react";
import { Panel } from "../ui/Panel";
import { TabPanel, Tabs } from "../ui/Tabs";
import { CustomTickets } from "./CustomTickets";
import { MyCustomRules } from "./MyCustomRules";
import type { EditTarget } from "./RuleAssistant";
import { SharedCustomRules } from "./SharedCustomRules";

export type CustomChecksTab = "mine" | "public" | "requests";

const TABS = [
  { id: "mine", label: "My checks" },
  { id: "public", label: "Public checks" },
  { id: "requests", label: "Requests to the tech team" },
] as const;

/**
 * Under the built-in catalogue: the officer's own custom checks, the public ones from everybody, and the requests sent
 * to the tech team. The page only renders this when /me says custom rules are on. `tab` is controlled so the popup can
 * land the officer on "My checks"; each time `revealKey` changes the section scrolls into view and takes focus.
 */
export function CustomChecksSection({ admin, tab, onTabChange, revealKey, onNew, onEdit }: { admin: boolean; tab: CustomChecksTab; onTabChange: (t: CustomChecksTab) => void; revealKey: number; onNew: () => void; onEdit: (r: EditTarget) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (revealKey === 0) return;
    // after the popup has handed focus back to its opener
    const t = setTimeout(() => {
      ref.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      ref.current?.focus({ preventScroll: true });
    }, 0);
    return () => clearTimeout(t);
  }, [revealKey]);

  return (
    <div ref={ref} id="custom-checks" tabIndex={-1} className="scroll-mt-4 focus:outline-none">
      <Panel title="Custom checks" eyebrow="Written by officers, not reviewed by the development team">
        <div className="space-y-5">
          <Tabs group="custom" label="Custom checks" tabs={[...TABS]} value={tab} onValueChange={(t) => onTabChange(t as CustomChecksTab)} />
          <TabPanel group="custom" id="mine" value={tab}>
            <MyCustomRules onNew={onNew} onEdit={onEdit} />
          </TabPanel>
          <TabPanel group="custom" id="public" value={tab}>
            <SharedCustomRules admin={admin} />
          </TabPanel>
          <TabPanel group="custom" id="requests" value={tab}>
            <CustomTickets admin={admin} />
          </TabPanel>
        </div>
      </Panel>
    </div>
  );
}
