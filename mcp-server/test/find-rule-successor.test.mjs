import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Rule lookup preserve human-only identity merge", () => assertAdmits(["find-rule", "confirm-merge"]));
