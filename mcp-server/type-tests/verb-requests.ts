// Positive request samples checked against generated verb contracts.
import type { VerbInputs } from "../.generated/verb-inputs.js";

export const bootRequest: VerbInputs["standing-context"] = { detail: "boot", page: 1 };

export const partyRequest: VerbInputs["add-party"] = { idempotency_key: "contract-test", name: "Example", kind: "org" };
