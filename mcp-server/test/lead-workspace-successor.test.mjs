import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Leads preserve existing rule, contact and workspace actions", () => assertAdmits(["find-rule", "teach", "record-lead-contact", "claim-lead", "link-lead-client", "update-lead", "lead-board", "unfinished-work"]));
