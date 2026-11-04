"use client";

import { Lock } from "lucide-react";
import { useState } from "react";
import { useSWRConfig } from "swr";
import { CustomTickets, TICKETS_KEY } from "@/components/custom-rules/CustomTickets";
import { CUSTOM_RULES_KEY, MyCustomRules } from "@/components/custom-rules/MyCustomRules";
import { RuleAssistant, type EditTarget } from "@/components/custom-rules/RuleAssistant";
import { SharedCustomRules } from "@/components/custom-rules/SharedCustomRules";
import { Loading } from "@/components/StateViews";
import { EmptyState, SectionHeading, Stagger, TabPanel, Tabs } from "@/components/ui";
import { useMeStatus } from "@/lib/useMe";

type TabId = "new" | "mine" | "public" | "requests";

const TABS = [
  { id: "new", label: "New rule" },
  { id: "mine", label: "My rules" },
  { id: "public", label: "Public rules" },
  { id: "requests", label: "Requests to the tech team" },
] as const;

export default function CustomRulesPage() {
  const { me, loading } = useMeStatus();
  const { mutate } = useSWRConfig();
  const [tab, setTab] = useState<TabId>("new");
  const [edit, setEdit] = useState<EditTarget | null>(null);

  if (loading) return <Loading label="Loading" />;
  if (!me?.custom_rules) {
    return (
      <Stagger className="space-y-6">
        <SectionHeading level={1} size="page">
          Custom rules
        </SectionHeading>
        <EmptyState
          icon={<Lock aria-hidden className="h-6 w-6" />}
          title="Custom rules are not available"
          text="They are switched off on this deployment, or you are not signed in with a personal account. Ask the administrator."
        />
      </Stagger>
    );
  }

  return (
    <Stagger className="space-y-6">
      <SectionHeading
        level={1}
        size="page"
        description="Describe a check in plain English. The assistant turns it into one of four fixed kinds of rule, shows you exactly what it will and will not check, and tests it on stored cases before you save. A rule is private to you until you make it public."
      >
        Custom rules
      </SectionHeading>
      <Tabs group="custom" label="Custom rules" tabs={[...TABS]} value={tab} onValueChange={(t) => setTab(t as TabId)} />
      <TabPanel group="custom" id="new" value={tab}>
        <RuleAssistant
          key={edit?.id ?? "new"}
          edit={edit}
          onSaved={() => {
            void mutate(CUSTOM_RULES_KEY);
            void mutate(TICKETS_KEY);
          }}
          onCancelEdit={() => setEdit(null)}
          onTicket={() => void mutate(TICKETS_KEY)}
          onOpenRules={() => {
            setEdit(null);
            setTab("mine");
          }}
        />
      </TabPanel>
      <TabPanel group="custom" id="mine" value={tab}>
        <MyCustomRules
          onNew={() => setTab("new")}
          onEdit={(r) => {
            setEdit(r);
            setTab("new");
          }}
        />
      </TabPanel>
      <TabPanel group="custom" id="public" value={tab}>
        <SharedCustomRules admin={me.admin} />
      </TabPanel>
      <TabPanel group="custom" id="requests" value={tab}>
        <CustomTickets admin={me.admin} />
      </TabPanel>
    </Stagger>
  );
}
