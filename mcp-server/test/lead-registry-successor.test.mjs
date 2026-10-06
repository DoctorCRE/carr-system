import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Lead and invoice actions retain their admitted contracts", () => assertAdmits(["record-lead-contact", "advance-leads", "approve-lead-draft", "approve-lead-move", "undo-lead-move", "record-deal-invoice", "invoice-close-queue", "undo-invoice-close"]));
