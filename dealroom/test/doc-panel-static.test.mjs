import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

test("Doc panel script declares the keyboard/focus/modality contract V5-J101 requires", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  assert.match(js, /aria-haspopup/);
  assert.match(js, /aria-expanded/);
  assert.match(js, /aria-controls/);
  assert.match(js, /aria-modal/);
  assert.match(js, /claimInert\(/, "background regions must be claimed inert while the panel is a phone-width modal");
  assert.match(js, /key !== 'Escape'/);
  assert.match(js, /escapeShouldClose\(/, "Escape must go through the shared pinned/not-pinned rule, not a local re-implementation");
  assert.match(js, /docPanelModality\(/);
  assert.match(js, /focusWithoutScrolling\(toggle\)/, "focus must return to the control that opened the panel on close");
  assert.match(js, /focusWithoutScrolling\(dom\.title\)/, "opening the panel must move focus into it, onto its heading");
  assert.match(js, /registerDocPanelSource/);
  assert.match(js, /runCommand/, "the panel must call the shared command registry, never a client verb directly");
  assert.doesNotMatch(js, /client\.setNextStep\(/, "doc-panel.js must not call the client directly — that would break UI/Doc parity");
  assert.doesNotMatch(js, /client\.addDealNote\(/);

  // Reuse the record panel's own tab-stop rule rather than a second,
  // independently-drifting implementation of the same heading-is-not-a-stop
  // case (the source of the Shift+Tab-escapes-to-the-toggle defect).
  assert.match(js, /import\s*\{\s*panelTabTarget\s*\}\s*from\s*'\.\/workspace-business-model\.js'/);
  assert.match(js, /panelTabTarget\(\{/);

  // The toggle must no longer be excluded from the inert sweep — that
  // exclusion was exactly what let focus escape onto it.
  assert.doesNotMatch(js, /el\s*!==\s*toggle\s*&&\s*el\s*!==\s*panel/, "toggle must not be excluded from the regions the trap inerts");

  // The Doc panel must be reachable while a native <dialog> (e.g. #dealDialog,
  // #formDialog) is shown modally — it has to reparent into whichever one is
  // open, since showModal() makes everything outside that dialog's own
  // subtree inert.
  assert.match(js, /MutationObserver/);
  assert.match(js, /dialog\[open\]/);

  // Round 3: claim/release through the shared reference-counted registry,
  // not a blind un-set and not even a per-file snapshot/restore, so Doc can
  // never undo another panel's still-active claim on a shared region no
  // matter which of the two claimed or released first.
  assert.match(js, /import\s*\{\s*claimInert,\s*releaseInert,\s*isClaimedByOther\s*\}\s*from\s*'\.\/inert-registry\.js'/);
  assert.doesNotMatch(js, /region\.inert\s*=\s*false/, "doc-panel.js must never write region.inert directly — only through the shared registry");
  assert.doesNotMatch(js, /region\.inert\s*=\s*true/, "doc-panel.js must never write region.inert directly — only through the shared registry");
});

test("Doc panel's accessible name is the persona 'Dr. CRE'; the visible label stays the 'Doc' nickname", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  // Toggle: accessible name via aria-label, visible text stays "Doc".
  assert.match(js, /aria-label',\s*'Dr\. CRE'/);
  assert.match(js, /doc-toggle-label[^<]*>Doc</);
  // Panel: aria-labelledby resolves to a name containing "Dr. CRE" (the
  // eyebrow), while the visible heading itself stays "Doc".
  assert.match(js, /aria-labelledby',\s*'docPanelEyebrow docPanelTitle'/);
  assert.match(js, /id="docPanelEyebrow">Dr\. CRE</);
  assert.match(js, /id="docPanelTitle"[^>]*>Doc</);
});

test("a failed or unavailable Doc attempt offers Retry, and the retry reuses the same idempotency key", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  assert.match(js, /doc-log-retry/);
  assert.match(js, /idempotencyKey:\s*receipt\.idempotencyKey/);
});

test("round 3: retry state is captured per failed line, never a single shared mutable slot", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  // The round-2 design kept ONE `state.lastAttempt` object that every retry
  // closure read at click time — an older Retry after a newer success or
  // failure would read whatever the LATEST attempt had written there. It is
  // gone entirely now: each failed line's retry closes over its OWN captured
  // object instead.
  assert.doesNotMatch(js, /state\.lastAttempt/, "must not reintroduce a single shared retry slot");
  assert.match(js, /const captured\s*=\s*\{\s*command,\s*text,\s*context,\s*idempotencyKey/, "each failed attempt must capture its own command/text/context/key");
  assert.match(js, /retry:\s*\(\)\s*=>\s*attempt\(captured\)/, "retry must replay the CAPTURED attempt, not re-read current state");
  // attempt() must never call source.getContext() itself — every context it
  // uses must be handed to it by the caller, so a retry's context can never
  // silently drift to whatever deal happens to be open at click time.
  const attemptBody = js.slice(js.indexOf("async function attempt("), js.indexOf("async function handleSubmit("));
  assert.doesNotMatch(attemptBody, /source\.getContext\(\)\s*\|\|\s*\{\}/, "attempt() must not re-read the live context internally");
});

test("round 3: the toggle is inert whenever another modal (the record panel) is covering the page and Doc is closed", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  assert.match(js, /externalModalActive/);
  assert.match(js, /region\s*!==\s*toggle\s*&&\s*isClaimedByOther\(region,\s*OWNER\)/);
});

test("round 3: a host switch releases every region Doc currently manages before moving", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  assert.match(js, /function releaseAllManaged/);
  assert.match(js, /releaseAllManaged\(\)/);
  // claimedByMe must be iterable (a host switch needs to walk it) — a plain
  // Set of the regions Doc itself currently holds a registry claim on.
  assert.match(js, /const claimedByMe = new Set\(\)/);
});

test("round 3: Doc and the record panel share ONE reference-counted inert registry, so neither's claim/release can stomp the other's still-active one", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  const registry = await readFile(`${ROOT}/js/inert-registry.js`, "utf8");
  const businessJs = await readFile(`${ROOT}/js/workspace-business.js`, "utf8");
  // The registry itself: reference-counted, not snapshot/restore-per-owner.
  assert.match(registry, /export function claimInert/);
  assert.match(registry, /export function releaseInert/);
  assert.match(registry, /export function isClaimedByOther/);
  assert.match(registry, /entry\.owners\.add\(ownerId\)/);
  assert.match(registry, /entry\.owners\.delete\(ownerId\)/);
  assert.match(registry, /if \(entry\.owners\.size > 0\) return/, "a region must stay inert as long as ANY owner still claims it");
  // Both panels route through the SAME module (not two independent copies —
  // that would recreate exactly the bug this exists to fix), each under its
  // own distinct owner id.
  assert.match(js, /const OWNER = 'doc'/);
  assert.match(businessJs, /import\s*\{\s*claimInert,\s*releaseInert\s*\}\s*from\s*"\.\/inert-registry\.js"/);
  assert.match(businessJs, /const PANEL_OWNER = "record-panel"/);
  assert.doesNotMatch(businessJs, /region\.inert\s*=\s*modal/, "the record panel must not write region.inert directly any more — only through the shared registry");
});

test("round 3: the 'Working on' label is refreshed whenever a host page's open deal changes, not only on open()", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  assert.match(js, /export function refreshDocContext/);
  assert.match(js, /currentRenderContext\s*=\s*renderContext/);
  const appJs = await readFile(`${ROOT}/js/app.js`, "utf8");
  assert.match(appJs, /refreshDocContext/, "a host page must actually call refreshDocContext when its open deal changes");
});

test("Doc panel styling respects reduced motion and does not hardcode a single theme's colors", async () => {
  const css = await readFile(`${ROOT}/css/doc-panel.css`, "utf8");
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  assert.match(css, /var\(--orange/, "must theme through the host page's tokens rather than a fixed hex");
  assert.match(css, /@media\(max-width:767px\)/, "must have an explicit phone-width layout, matching the record panel's own breakpoint");
});

test("every J101 surface loads the Doc panel script and its stylesheet", async () => {
  const pages = ["workspace.html", "business.html", "index.html"];
  for (const page of pages) {
    const html = await readFile(`${ROOT}/${page}`, "utf8");
    assert.match(html, /doc-panel\.css/, `${page} must load doc-panel.css`);
  }
  // index.html pulls doc-panel.js indirectly through app.js's own import, so
  // it is checked separately rather than for a duplicate <script> tag.
  const workspace = await readFile(`${ROOT}/workspace.html`, "utf8");
  const business = await readFile(`${ROOT}/business.html`, "utf8");
  assert.match(workspace, /src="\/js\/doc-panel\.js"/);
  assert.match(business, /src="\/js\/doc-panel\.js"/);
  const appJs = await readFile(`${ROOT}/js/app.js`, "utf8");
  assert.match(appJs, /from '\.\/doc-panel\.js'/);
  const indexHtml = await readFile(`${ROOT}/index.html`, "utf8");
  assert.doesNotMatch(indexHtml, /src="js\/doc-panel\.js"/, "index.html must not double-load doc-panel.js alongside app.js's import");
});

test("round 3 (non-blocking): the next-step form mints a new idempotency key only when resubmitted content actually differs", async () => {
  const appJs = await readFile(`${ROOT}/js/app.js`, "utf8");
  const fn = appJs.slice(appJs.indexOf("function nextStepForm("), appJs.indexOf("function parkDealForm("));
  assert.match(fn, /let lastAttempt = null/);
  assert.match(fn, /identicalRetry/);
  assert.match(fn, /idempotencyKey = identicalRetry \? lastAttempt\.idempotencyKey : uuidv4\(\)/);
});

test("round 3 (non-blocking): doc-panel.css defines .visually-hidden itself, so the composer label is hidden on every host page regardless of which other stylesheet loaded", async () => {
  const css = await readFile(`${ROOT}/css/doc-panel.css`, "utf8");
  assert.match(css, /\.visually-hidden\s*\{/);
  const indexHtml = await readFile(`${ROOT}/index.html`, "utf8");
  // index.html loads app.css + doc-panel.css only, never workspace-business.css.
  assert.doesNotMatch(indexHtml, /workspace-business\.css/);
});

test("commands.js never widens scope into Calls or Tours (V5-J101 must keep both inert)", async () => {
  const commands = await readFile(`${ROOT}/js/commands.js`, "utf8");
  assert.doesNotMatch(commands, /call[_-]?mode|startCall|recordCall|CallMode/i);
  assert.doesNotMatch(commands, /\btour\b/i);
});

test("the four new dealroom/test suites are registered in the one CI shim (workspace-surface-inventory.test.mjs clause)", async () => {
  const shim = await readFile(`${ROOT}/../mcp-server/test/workspace-command-center-browser.test.mjs`, "utf8");
  for (const suite of ["capability-seams", "commands", "doc-panel-model", "doc-panel-static", "inert-registry"]) {
    assert.match(shim, new RegExp(`dealroom/test/${suite}\\.test\\.mjs`), `${suite}.test.mjs must be imported by the CI shim`);
  }
});
