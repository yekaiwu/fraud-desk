import { redirect } from "next/navigation";

/** Custom checks used to have their own page; they now live on the check catalogue (old bookmarks land there). */
export default function CustomRulesRedirect(): never {
  redirect("/rules");
}
