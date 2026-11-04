import { PencilRuler } from "lucide-react";
import { customLabel } from "@/lib/customRules";
import { Badge } from "../ui/Badge";

/**
 * Marks a rule an officer wrote (not one the development team wrote and tested). With `info` the tooltip and the
 * screen-reader text say who wrote it and which version: "Custom, by alice, v2, unreviewed".
 */
export function CustomBadge({ info, className }: { info?: { author: string; version: number }; className?: string }) {
  return (
    <Badge variant="lane" icon={PencilRuler} className={className} title={info ? customLabel(info) : "Custom rule written by an officer, not reviewed by the development team"}>
      Custom
      {info ? <span className="sr-only">: {customLabel(info)}</span> : null}
    </Badge>
  );
}
