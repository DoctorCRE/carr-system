// Acceptance rule from jbookout/doctorcre-app main bdb0d0a1d2bcb1ae27834b2237981401b21edf36.
// js/email-followups.js:16-22,51. Keep failed-preview cards visible and dismissible.
const KINDS = {
  file_to_deal: { confirm: "confirmEmailFiling", verb: "confirm-email-filing", contract: "email-filing.v1" },
  possible_new_deal: { confirm: "confirmEmailFiling", verb: "confirm-email-filing", contract: "email-filing.v1" },
  next_step: {
    preview: "previewEmailNextStep",
    confirm: "confirmEmailNextStep",
    verb: "confirm-email-next-step",
    contract: "email-next-step.v1",
  },
  reply_draft: { verb: "submit-email-draft" },
  phase_move: {
    preview: "previewEmailDealProgress",
    confirm: "confirmEmailDealProgress",
    verb: "confirm-email-deal-progress",
    contract: "email-deal-progress.v1",
  },
};
export const acceptsConfirmVerb = (row: { kind: keyof typeof KINDS; confirm_verb: string | null }) =>
  row.confirm_verb === KINDS[row.kind].verb || (row.kind === "reply_draft" && row.confirm_verb === null);
