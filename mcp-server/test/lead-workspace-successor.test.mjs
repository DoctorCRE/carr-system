import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Leads preserve existing rule lookup, contact and workspace actions", () => assertAdmits(["find-rule", "record-lead-contact", "claim-lead", "link-lead-client", "update-lead", "lead-board", "unfinished-work"]));
