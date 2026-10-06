import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Unfinished work and Doc activity remain admitted together", () => assertAdmits(["unfinished-work", "read-doc-activity", "read-room-latest"]));
