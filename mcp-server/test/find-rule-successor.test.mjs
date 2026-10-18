import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Rule lookup and teaching preserve human-only identity merge", () => assertAdmits(["find-rule", "teach", "confirm-merge"]));
