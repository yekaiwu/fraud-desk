"use client";

import { useSWRConfig } from "swr";
import { Modal } from "../Modal";
import { CUSTOM_RULES_KEY } from "./MyCustomRules";
import { RuleAssistant, type EditTarget } from "./RuleAssistant";
import { TICKETS_KEY } from "./CustomTickets";

/**
 * The "Create a custom check" popup on the check catalogue: the rule assistant, for a new check or (with `edit`) a new
 * version of one of the officer's own. The Modal renders its children only while open, so closing it throws an unsaved
 * draft away and the next opening starts fresh. After a save the assistant's saved view stays in the popup; its "Go to
 * my checks" button closes it and hands over to `onOpenChecks`.
 */
export function CreateCustomCheckDialog({ open, edit, onClose, onCancelEdit, onOpenChecks }: { open: boolean; edit: EditTarget | null; onClose: () => void; onCancelEdit: () => void; onOpenChecks: () => void }) {
  const { mutate } = useSWRConfig();
  return (
    <Modal open={open} onClose={onClose} title="Create a custom check" wide>
      <RuleAssistant
        key={edit?.id ?? "new"}
        edit={edit}
        onSaved={() => {
          void mutate(CUSTOM_RULES_KEY);
          void mutate(TICKETS_KEY);
        }}
        onCancelEdit={onCancelEdit}
        onTicket={() => void mutate(TICKETS_KEY)}
        onOpenRules={onOpenChecks}
      />
    </Modal>
  );
}
