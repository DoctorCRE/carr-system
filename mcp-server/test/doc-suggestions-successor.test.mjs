import test from 'node:test';
import {assertAdmits} from './helpers/registry-admission.mjs';

test("Doc suggestions, session reads and Observatory retain their admitted contracts", () => assertAdmits(["suggest-doc-work", "schedule-board", "list-my-codex-sessions", "read-room-latest"]));
