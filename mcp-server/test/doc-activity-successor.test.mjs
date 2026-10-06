import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Doc activity remains admitted beside lead automation", () => assertAdmits(["read-doc-activity", "advance-leads", "lead-approval-queue"]));
