// Share one fixed Date clock without replacing elapsed-time timers.
import { mock } from "node:test";

export const NOW = Date.parse("2026-10-27T15:00:00.000Z");
mock.timers.enable({ apis: ["Date"], now: NOW });
