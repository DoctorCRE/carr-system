import { isCalendarDate } from "./calendar-date.ts";

type InvoiceEvidence = {
  native_ref: string;
  from_address: string;
  deal_name: string;
  occurred_at: string;
};

export function isInvoiceEvidence({ native_ref, from_address, deal_name, occurred_at }: InvoiceEvidence, now: number) {
  return (
    /^local-mail:\S/.test(native_ref || "") &&
    typeof deal_name === "string" &&
    !!deal_name.trim() &&
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(from_address || "") &&
    /^\d{4}-\d{2}-\d{2}T/.test(occurred_at || "") &&
    isCalendarDate(occurred_at.slice(0, 10)) &&
    Date.parse(occurred_at) <= now
  );
}
