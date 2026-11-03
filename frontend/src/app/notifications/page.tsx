"use client";

import { useState } from "react";
import { Stagger, SectionHeading } from "@/components/ui";
import { Feed } from "@/components/notifications/Feed";
import { Incidents } from "@/components/notifications/Incidents";
import { OpenAlerts } from "@/components/notifications/AlertCards";
import { MonitorStatus, ReadinessPanel } from "@/components/notifications/StatusPanels";
import { useRefreshBadge } from "@/lib/useNotificationSummary";

/**
 * Notifications: what the monitor is telling people, on the page, even when no outside channel is set up.
 * Alerts raise attention; a person looks and acts. Delivery to this page is not the same as someone watching it,
 * and the readiness panel says so.
 */
export default function NotificationsPage() {
  const refreshBadge = useRefreshBadge();
  // After any action every section reloads, so the feed, the alerts, the readiness items and the badge agree.
  const [tick, setTick] = useState(0);
  const changed = () => {
    refreshBadge();
    setTick((t) => t + 1);
  };
  return (
    <Stagger className="space-y-6">
      <SectionHeading level={1} size="page" description="Alerts, incidents and tests in one place, with what to do about them. Look here when the number on the tab is not zero.">
        Notifications
      </SectionHeading>
      <div className="space-y-6 lg:grid lg:grid-cols-[minmax(0,1fr)_24rem] lg:grid-rows-[auto_1fr] lg:items-start lg:gap-x-6 lg:gap-y-6 lg:space-y-0">
        <div className="min-w-0 lg:col-start-2 lg:row-start-1">
          <MonitorStatus onChanged={changed} tick={tick} />
        </div>
        <div className="min-w-0 space-y-6 lg:col-start-1 lg:row-span-2 lg:row-start-1">
          <OpenAlerts onChanged={changed} tick={tick} />
          <Feed onChanged={changed} tick={tick} />
          <Incidents onChanged={changed} tick={tick} />
        </div>
        <div className="min-w-0 lg:col-start-2 lg:row-start-2">
          <ReadinessPanel tick={tick} />
        </div>
      </div>
    </Stagger>
  );
}
