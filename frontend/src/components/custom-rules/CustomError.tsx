import Link from "next/link";
import { authoringDown, explainCustomError } from "@/lib/customRules";
import { ApiErrorException, type ApiErrorBody } from "@/lib/errors";
import { Notice } from "../ui/Notice";

export function bodyOf(error: unknown): ApiErrorBody {
  if (error instanceof ApiErrorException) return error.error;
  return { code: "internal_error", message: error instanceof Error ? error.message : "Unexpected error." };
}

/** True when the rule assistant cannot be used right now (budget used up, backend down): ideas can still go to the tech team. */
export function isAuthoringDown(error: unknown): boolean {
  return authoringDown(bodyOf(error).code);
}

/**
 * A failed custom-rule action, explained. A built-in duplicate links to the check in the catalogue (where its severity
 * is changed); `action` slots in the next step, such as sending the idea to the tech team.
 */
export function CustomError({ error, action }: { error: unknown; action?: React.ReactNode }) {
  const body = bodyOf(error);
  const { title, detail } = explainCustomError(body);
  const rule = body.code === "duplicate_builtin" && typeof body.details?.rule === "string" ? body.details.rule : null;
  return (
    <Notice variant="error" title={title} action={action}>
      {detail ? <p className="break-words">{detail}</p> : null}
      {rule ? (
        <p className="mt-1">
          <Link href={`/rules?check=${encodeURIComponent(rule)}`} className="font-medium text-brand-2 underline underline-offset-2">
            Open {rule} in the check catalogue
          </Link>
        </p>
      ) : null}
    </Notice>
  );
}
