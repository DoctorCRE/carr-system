import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Tour evidence and feedback remain admitted together", () => assertAdmits(["append-tour-source-evidence", "read-tour-feedback"]));
