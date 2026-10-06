// The browser half of the Home unit's evidence had no runner: dealroom/ carries no package.json,
// and ops/ci.sh's unit loop covers mcp-server, control-room and workspace only — so every
// browser-model, scope, phase, freshness and link assertion sat outside the merge gate.
//
// This file is the single CI entry for the dealroom/test suites. It imports them where they already
// live; their tests, fixtures and relative paths are unchanged, and nothing is copied here. Every
// suite resolves its own paths from import.meta.url, so they behave identically under this runner.
// Added to the existing `node --test test/*.test.mjs` glob in mcp-server/package.json; no new
// check class, script or ops/ci.sh edit.
//
// IT IMPORTS ALL OF THEM, AND THAT IS ENFORCED. When this shim carried only the two Home suites,
// the other four — boot-mode, lead-board-client, lead-board-static, system-work-branding, twelve
// tests including the lead board's own reduced-motion and accessible-control assertions — were
// collected by nobody and ran at no merge gate. workspace-surface-inventory.test.mjs now READS
// dealroom/test and fails if any *.test.mjs file there is not imported below, so the next suite
// added to that directory cannot silently fall outside the gate the way four did.
import "../../dealroom/test/boot-mode.test.mjs";
import "../../dealroom/test/capability-seams.test.mjs";
import "../../dealroom/test/commands.test.mjs";
import "../../dealroom/test/doc-panel-model.test.mjs";
import "../../dealroom/test/doc-panel-static.test.mjs";
import "../../dealroom/test/inert-registry.test.mjs";
import "../../dealroom/test/lead-board-client.test.mjs";
import "../../dealroom/test/lead-board-static.test.mjs";
import "../../dealroom/test/system-work-branding.test.mjs";
import "../../dealroom/test/doc-panel-real-browser-journey.test.mjs";
import "../../dealroom/test/workspace-command-center-model.test.mjs";
import "../../dealroom/test/workspace-command-center-static.test.mjs";
