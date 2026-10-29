import { CheckCheck, Clock3, FileCheck2, Scale } from "lucide-react";
import { summarise } from "@/lib/effectiveness";
import { formatRatio, formatSeconds } from "@/lib/format";
import { plural } from "@/lib/labels";
import type { EffectivenessResponse } from "@/lib/types";
import { ExplainTile } from "../explain/PerformanceExplainers";
import { Panel } from "../ui/Panel";
import { Stat } from "../ui/Stat";
import { StackedBar } from "./StackedBar";

/** The four headline tiles, every figure derived from the response the page already has. */
export function PerformanceSummary({ data }: { data: EffectivenessResponse }) {
  const s = summarise(data.rules);
  const icon = "h-3.5 w-3.5 text-brand-2";
  return (
    <Panel padding="md">
      <div role="group" aria-label="Summary" className="grid grid-cols-2 gap-x-5 gap-y-6 sm:gap-x-8 xl:grid-cols-4 [&>div]:min-w-0">
        <div>
          <Stat
            icon={<FileCheck2 aria-hidden className={icon} />}
            label="Cases decided"
            labelAddon={<ExplainTile topic="decided" data={data} />}
            value={data.cases_decided}
            hint={`of ${data.cases_total} ${plural(data.cases_total, "case")} in this view`}
          />
        </div>
        <div>
          <Stat
            icon={<CheckCheck aria-hidden className={icon} />}
            label="Checks that fired"
            labelAddon={<ExplainTile topic="fired" data={data} />}
            value={s.fired}
            hint={`of ${s.registered} ${plural(s.registered, "check")}, ${s.flagsRaised} ${plural(s.flagsRaised, "flag")} raised`}
          />
        </div>
        <div>
          <Stat
            icon={<Scale aria-hidden className={icon} />}
            label="Confirmed vs dismissed"
            labelAddon={<ExplainTile topic="share" data={data} />}
            value={s.confirmedShare === null ? "-" : `${formatRatio(s.confirmedShare)} confirmed`}
            hint={s.decisions === 0 ? "No flags decided yet" : `${s.confirmed} confirmed, ${s.dismissed} dismissed`}
          />
          {s.decisions > 0 ? <StackedBar r={{ confirmed: s.confirmed, dismissed: s.dismissed, pending: 0, open: 0 }} showText={false} className="mt-2 max-w-48" /> : null}
        </div>
        <div>
          <Stat
            icon={<Clock3 aria-hidden className={icon} />}
            label="Median time to decide"
            labelAddon={<ExplainTile topic="median" data={data} />}
            value={formatSeconds(data.median_seconds_to_verdict)}
            hint={data.median_seconds_to_verdict === null ? "Appears once cases are closed" : "across decided cases"}
          />
        </div>
      </div>
    </Panel>
  );
}
