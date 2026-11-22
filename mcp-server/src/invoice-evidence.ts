// Validate local invoice evidence before either capture route saves it.
import { isCalendarDate } from "./calendar-date.ts";

type InvoiceEvidence = {
  native_ref: string;
  from_address: string;
  deal_name: string;
  occurred_at: string;
};

function isInvoiceSender(value: string) {
  const address = String(value || "");
  return /^[^\s@]+@[^\s@]+$/.test(address) && address.lastIndexOf(".", address.length - 2) > address.indexOf("@") + 1;
}

export function isInvoiceEvidence({ native_ref, from_address, deal_name, occurred_at }: InvoiceEvidence, now: number) {
  return (
    /^local-mail:\S/.test(native_ref || "") &&
    typeof deal_name === "string" &&
    !!deal_name.trim() &&
    isInvoiceSender(from_address) &&
    /^\d{4}-\d{2}-\d{2}T/.test(occurred_at || "") &&
    isCalendarDate(occurred_at.slice(0, 10)) &&
    Date.parse(occurred_at) <= now
  );
}
