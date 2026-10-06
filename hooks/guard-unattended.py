#!/usr/bin/env python3
"""guard-unattended.py — the PreToolUse deny gate (idea-bank #32, second job).

WHY THIS EXISTS. Everything else that constrains a session in this system is
compliance-dependent: doctrine, taught rules, the compiled-rules recitation, the
writing-lint gate. All of them work only if the session chooses to obey. On
2026-08-02 an audit found ten claims relayed as verified that were not, which is
what compliance-dependent control looks like when it fails. A hook is the only
mechanism here the model cannot reason its way around, because the harness runs
it, not the model.

WHAT IT BLOCKS. Five classes that are never correct for a session to do on its
own, unattended or not:
  1. Destructive filesystem writes outside the sanctioned scratch zones.
  2. History-rewriting or force git operations.
  3. Reads of private key material.
  4. Network sends to hosts outside the known set (the exfiltration guard).
  5. Destructive SQL (DROP / TRUNCATE / unqualified DELETE or UPDATE).

WHAT IT DELIBERATELY DOES NOT BLOCK. Ordinary database WRITES. Step 1 of the
nightly chain is `cadence_engine.py --apply`, which writes by design. A naive
"no writes when unattended" rule would kill the chain at 2am. The distinction
this gate draws is destructive-or-exfiltrating, never write-versus-read.

THE NIGHTLY CHAIN IS EXPLICITLY SAFE. Its command string is
`cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo "direct script exit=$?"`
and it carries a persisted permission approval that must stay byte-identical.
It matches no pattern below. It is also allowlisted by exact string as a second
belt, so a future pattern cannot accidentally catch it.

KNOWN LIMIT, STATED HONESTLY. This sees the command the SESSION issues, not what
a script does internally. `nightly.sh` runs git, pg_dump and R2 uploads inside
itself and this gate never sees them. That is the correct scope: the threat model
is a session taking a destructive action, not the reviewed scripts in this repo.

FAILS OPEN, ON PURPOSE. Any internal error allows the call. A gate that wedges
the 2am chain costs more than the marginal safety of failing closed, on a
single-operator machine that is not a hostile environment. Every allow-on-error
is logged so a silently-degraded gate is still discoverable.

DISABLE FAST: remove the hooks block from settings.json, or `chmod -x` this file
(a non-executable hook errors, and this gate fails open).
"""

import json
from datetime import datetime, timezone
import ipaddress
import os
import re
import shlex
import sys
from urllib.parse import urlsplit

# The shared "tell a command apart from the prose it carries" helper, already
# used by the writer and staging-attribution gates. Imported rather than copied
# for the reason its own docstring gives: two copies of "what counts as inert"
# drift silently, because each copy still passes its own tests.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cmd_text import strip_inert_text  # noqa: E402
# Shared with hooks/record-home-gate.py, which refuses the FILE-TOOL spelling of
# the same write. One memory, so a record refused through either door is
# recognised at the other (rule 76a53dfe).
from refused_content import REFUSAL, remember_refusal, was_refused  # noqa: E402


def heredoc_body(cmd):
    """The text a heredoc feeds to a program — the payload of a shell write.

    Only the body: the command around it is the delivery mechanism, and the
    rule is about the RECORD being hidden, not about how it travelled.
    """
    match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1\n(.*?)\n\s*\2\s*$",
                      cmd or "", re.S | re.M)
    return match.group(3) if match else ""

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")
DELEGATION_STATE = os.path.join(REPO, "out", "delegation-gate-state.json")
DELEGATION_LOCK = DELEGATION_STATE + ".lock"

# The nightly chain, verbatim. Belt and braces: it matches nothing below, and it
# is also allowlisted so it can never be caught by a pattern added later.
ALLOW_EXACT = {
    'cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo "direct script exit=$?"',
    "cd ~/carr-system && ./run.sh health",
}

# Paths a session may freely destroy things inside.
SAFE_ZONES = (
    "/private/tmp/", "/tmp/", "/var/folders/",
    "carr-system/out/", "carr-system/_to_delete/", "_to_delete/",
    "scratchpad", "Graph.tmp",
)

# Hosts this system legitimately talks to.
KNOWN_HOSTS = (
    # api.doctorcre.com is the SAME Worker as api.practicecre.com — both are custom
    # domains on carr-mcp. It became the PRIMARY name on Joe's 2026-08-01 domain
    # ruling, which reached wrangler.toml and the Worker routes but never reached
    # this list, so calls to the primary domain were blocked until 2026-08-03.
    # Microsoft identity + Graph: the carr.us mailbox is Microsoft 365, and the
    # draft transport talks to these two and nothing else on Microsoft's side.
    "login.microsoftonline.com", "graph.microsoft.com", "outlook.office365.com",
    # The staging Worker's ONLY address. It is deliberately workers.dev and not a
    # custom domain: on 2026-08-13 a staging deploy without `routes = []`
    # inherited all three production custom domains and served api.doctorcre.com
    # from the empty staging database for about two minutes. Staging now attaches
    # to workers.dev alone, which ops/deploy-attachment-check.py verifies before
    # any non-production deploy — so this hostname is exactly how a session
    # reaches staging WITHOUT touching production, and blocking it pushes anyone
    # verifying a staging deploy toward the production URL instead. That is the
    # confusion the incident was made of, which is why it is listed here.
    "carr-mcp-staging.joe-bookout-carr-us.workers.dev",
    # Staging app Worker, CARR-owned, no client data.
    "doctorcre-app-staging.joe-bookout-carr-us.workers.dev",
    # nodejs.org: the official Node.js download host, and node is INFRASTRUCTURE
    # for this repo rather than a research read — mcp-server/local-verb.mjs is the
    # only route from a terminal or an unattended job to the record verbs, and
    # ops/ci.sh needs it too. Dell's Mac has no node, no Homebrew, and no sudo to
    # install one, so both were simply dead there: `./run.sh call` exits with
    # FileNotFoundError 'node' and local CI cannot run at all. This is the same
    # class as console.neon.tech above — a fixed host somebody decides once — and
    # the file's own header reserves the CODE list for exactly that, keeping the
    # record-derived half for client practice domains. Downloads are checksum
    # verified against the SHASUMS256.txt published beside the tarball.
    # registry.npmjs.org travels with it: node alone does not run local-verb.mjs,
    # which imports @neondatabase/serverless and the MCP SDK. Installs go through
    # `npm ci` against mcp-server/package-lock.json, so every package is pinned and
    # integrity-checked by npm from hashes already committed to this repo — the
    # lockfile is the review, not the network call.
    "nodejs.org", "registry.npmjs.org",
    # DoctorCRE's production app is CARR-owned infrastructure; release checks
    # and the signed-out progress-board check read this exact host.
    "api.practicecre.com", "api.doctorcre.com", "app.doctorcre.com",
    "api.anthropic.com", "console.neon.tech",
    "neon.tech", "cloudflareapi.com", "cloudflare.com", "r2.cloudflarestorage.com",
    "googleapis.com", "github.com", "api.github.com", "hc-ping.com",
    # Dot relay uses the Slack Web API; its user token stays in ~/.hermes/.env.
    # This host is fixed infrastructure, not a record-derived practice domain.
    "slack.com",
    "npiregistry.cms.hhs.gov", "download.cms.gov",
    # raw.githubusercontent.com: loop #163 named its absence as the gap forcing
    # the gh-api workaround for plain changelog reads. Added 2026-08-06 with the
    # WebFetch widening.
    "raw.githubusercontent.com",
    # Research-read hosts, added 2026-08-06 night on Joe's direct order to read
    # the architecture-research shortlist ("read each one 1 at a time") — the
    # first legitimate tuning of the widened WebFetch gate, hours after it
    # shipped. All are read-only documentation/paper hosts.
    # claude.com sits beside anthropic.com for the same reason and the same
    # owner, and its absence had a daily cost: the standard Claude Code
    # attribution line links there, so every `gh pr create` carrying it was
    # refused as an unrecognised host. That happened three times in one session
    # on 2026-08-14, each time to a real PR. The available workaround is to
    # strip the link, which quietly drops the attribution it exists to give.
    "arxiv.org", "anthropic.com", "claude.com", "humanlayer.dev", "mem0.ai",
    "langchain.com", "emergentmind.com",
    # TypeSafe, added 2026-09-17 on Joe's ruling. docs.typesafe.ai is the
    # documentation host and is a plain research read like the row above it;
    # api.typesafe.ai is the inference endpoint for Jev, a model that takes text
    # plus typed questions and returns probabilities rather than written text.
    # Joe ruled the same day that CARR content INCLUDING CLIENT AND DEAL
    # MATERIAL may be sent there, overruling this session's reading that
    # third-party client confidentiality was a floor: his reasoning is that the
    # practice already sends the same material to several model vendors, so
    # singling this one out protects nothing. That makes api.typesafe.ai a
    # CONTENT-BEARING host, unlike every research host listed above it, which is
    # why it is called out here rather than folded into that line. It is listed
    # in CODE rather than derived from the record because it is fixed
    # infrastructure somebody decides once, which is what this half of the list
    # is reserved for.
    "docs.typesafe.ai", "api.typesafe.ai",
    # blotato.io: the media-upload backend of the ALREADY-SANCTIONED Blotato
    # connector. Its create-post tool takes public mediaUrls, and the only way
    # to get a local PNG there is the presigned PUT its own tool description
    # prescribes ("Use curl ... --data-binary"). The network gate landed
    # 2026-08-02 (ea2c78c) and this host was never listed, which SILENTLY BROKE
    # the image half of `social-batch-weekly` — the 7/31 batch had uploaded 18
    # graphics fine, and the 2026-08-07 run was the first weekly batch after the
    # gate and hit the wall on its first upload. Restored here rather than
    # shipping a batch with no graphics, which would fail the mandatory
    # format-rotation gate in social-media-workflow.md. Scope is narrow by
    # construction: the PUT targets a short-lived presigned URL this session
    # minted seconds earlier through the connector, and carries a PNG we just
    # rendered. Added 2026-08-07 by the social-batch-weekly run; flagged to Joe
    # in that run's output because a scheduled job widening a security gate is
    # exactly the kind of change that should not pass unannounced.
    "blotato.io",
    # backend.blotato.com: the POSTING API host of the same already-sanctioned
    # Blotato connector whose media host sits on the line above. Added
    # 2026-08-21 on Joe's explicit in-session approval, by the
    # social-batch-weekly run, after the MCP connector that every batch through
    # 2026-08-14 used vanished from the runtime — leaving
    # claude-tree/skills/social-media-manager/scripts/blotato.sh, which this
    # repo already carries and which calls https://backend.blotato.com/v2, as
    # the only remaining route. That route is BETTER for an unattended run than
    # the connector was: BLOTATO_API_KEY is a stored env var in ~/.zprofile, so
    # it satisfies the rule that no unattended path may depend on an
    # interactive credential.
    #
    # SCOPE, stated because an allowlist entry is a standing permission:
    # scheduling review-drafts that Joe approves inside Blotato before anything
    # publishes, and READING the queue. The read half is why this was urgent —
    # the week-of-8/10 batch was verified as scheduled and then silently
    # vanished from the queue, and with no route to Blotato nobody could see
    # that it had happened.
    "backend.blotato.com",
    # huggingface.co: whisper.cpp model downloads for the dictation rig
    # (ggml-large-v3-turbo). Added 2026-08-07 on Joe's explicit go in the
    # dictation-rig build session; read-only model fetches into ~/.cache.
    "huggingface.co",
    # api.elevenlabs.io: Doc's production renderer. build-voice-corpus.py exists
    # for exactly this ("once ElevenLabs does production rendering, render speed
    # stops constraining the SOURCE material", Joe 2026-08-08) and the 193-line
    # corpus was built to be uploaded there. The guard blocked the host outright,
    # so the step the corpus was made for could not run. Added 2026-08-10 while
    # Joe was live in-session ("we can go through eleven labs testing with Doc's
    # voice"), and flagged to him in that turn rather than passing unannounced —
    # widening a security gate is not a silent act.
    #
    # SCOPE, stated because an allowlist entry is a standing permission: this is
    # the API host for voice creation and text-to-speech renders of a voice we
    # designed. Doc has no real-person referent (voice doctrine section 3), so
    # nothing here is a person's voice. Audio uploaded is synthetic Doc, raw and
    # pre-mastering by design. NOTE FOR ANY FUTURE READER: the same doctrine
    # section warns ElevenLabs retains a perpetual licence over the underlying
    # voice model, so exclusivity of Doc as a brand asset is an OPEN question,
    # not one this entry settles.
    "api.elevenlabs.io", "elevenlabs.io",
    # x.com / twitter.com: X retrieval is standing doctrine (rule 57d13061,
    # Grok-first as of 2026-08-07) and Joe hands sessions x.com links directly.
    # The guard was blocking the Grok CLI invocation itself because the link
    # appears in the command text. Read-only retrieval; Claude still writes all
    # content and nothing posts to X from a session. Added 2026-08-07 while Joe
    # was live in-session ("study this article for our system").
    "x.com", "twitter.com",
    # ── Client-verification sources, added 2026-08-07 on Joe's order after the
    # weekly deal-history research slice ran half-blind. That run could reach
    # NPPES and nothing else: no entity filing, no licence status, no primary
    # source of any kind. Four of fifteen records were filed found:false partly
    # because the registry that would have settled them was unreachable, and
    # every website fact in the run rests on a search-engine summary — which is
    # precisely what rule 94806da2 says not to let stand.
    #
    # All read-only public-record lookups. Each hostname was verified live
    # before being added rather than recalled: a guessed host in an allowlist is
    # dead weight at best, and at worst names a domain someone else can register.
    #
    # State entity registries — the "is this a real company and what is its
    # legal name" question that every client identity check runs through.
    "sunbiz.org",              # FL Division of Corporations (search.sunbiz.org)
    "dos.fl.gov",              # FL Dept of State, the Sunbiz parent
    "arc-sos.state.al.us",     # AL SOS entity search (legacy CGI, NOT under alabama.gov)
    # Licensing boards. alabama.gov is deliberately the whole domain rather than
    # one host per board: every AL board lives on it (chiro., optometry.,
    # asbvme., sos., licensesearch., inform.) and CARR's verticals will want
    # boards nobody has needed yet. One entry beats a dozen that drift apart.
    "alabama.gov",
    "dentalboard.org",         # Board of Dental Examiners of Alabama (a .org, not a .gov)
    # The AL dental board hosts its actual licensee lookup on a third-party
    # vendor, so dentalboard.org alone does NOT make dental licence checks work.
    # Scoped to the board's own host rather than igovsolution.net, which serves
    # many states' boards — this grant buys Alabama and nothing else. Widen only
    # if another state's board is ever needed. Joe's go, 2026-08-07.
    "bdeal.igovsolution.net",
    "albme.gov",               # AL Board of Medical Examiners
    # Alabama's official e-government portal (state contract, Tyler/NIC). Added
    # 2026-08-07 on Joe's go, one exchange after the rest of this block, because
    # verifying the block found the gap: the AL CHIROPRACTIC board publishes no
    # lookup on chiro.alabama.gov and routes to this portal instead, and it is
    # the one AL board absent from the unified licensesearch.alabama.gov (that
    # path returns HTTP 500). Without this entry a chiropractor's licence cannot
    # be verified in Alabama at all — the live gap behind the C-130 Nina Calloway
    # npi:found=false row of 2026-08-07.
    #
    # UNLIKE the igovsolution entry above, this one CANNOT be narrowed: the
    # boards sit on PATHS of the apex domain (/asbce/, /sos/) rather than on
    # per-board subdomains, and this matcher tests hosts, not paths. So the
    # grant is the whole portal or nothing. Accepted as the same trust class as
    # alabama.gov — it is the state's own contracted portal — and noted here
    # plainly rather than left to look like a deliberately loose entry.
    "alabamainteractive.org",
    "doh.state.fl.us",         # FL DOH MQA licence search (appsmqa./mqa-internet./mqa-vo.)
    "flhealthsource.gov",      # FL DOH consumer-facing licence portal
    # Professional directories, ranked BELOW the registries above: ada.org is an
    # association roster and healthgrades.com is a commercial aggregator
    # carrying stale and advertised entries. Corroboration only, never the sole
    # source for an identity claim.
    "ada.org",
    "healthgrades.com",
    # NOT added, and deliberately: individual practice websites. They are the
    # largest remaining gap in client verification and an allowlist cannot close
    # it — every client has a different domain, so the list would have to grow
    # by one entry per client forever. linkedin.com is also omitted: it blocks
    # automated fetches behind a login wall, so listing it would buy nothing.
    #
    # hermes-agent.nousresearch.com: the vendor host serving the Hermes Agent
    # installer and its documentation. Hermes is the runtime the 2026-08-12
    # frontier council cleared for an R0 evaluation (decision 94c0206f,
    # "Present Hermes on Nous Portal Cloud as a constrained council evaluation
    # candidate") — read-only and synthetic, with live CARR data held behind a
    # second Joe decision. The install was blocked here on 2026-08-16 and Joe
    # approved this entry in session the same day, after being told the host,
    # the installer, and that a gate edit was the price.
    #
    # SCOPE, stated because an allowlist entry is a standing permission: this
    # buys the installer download and the docs on that host. It buys Hermes
    # NOTHING inside CARR. The R0 pilot profile and its three permitted read
    # verbs are unbuilt planning fixtures in phase0/, so there is nothing for a
    # Hermes runtime to connect to, and connecting it to the live record layer
    # under Joe's own profile is the thing all three council seats refused. A
    # future session finding Hermes reaching CARR has found a defect, not a
    # permission granted here. api.x.ai is deliberately absent: the sanctioned
    # sign-in is browser OAuth against Joe's own subscription, which runs in his
    # browser rather than through a session's network calls.
    "hermes-agent.nousresearch.com",
    # tailc8cc93.ts.net: Joe's own private Tailscale tailnet (his MagicDNS
    # domain), reachable only from devices signed into or shared onto that
    # tailnet — never public. Added 2026-09-23 so his Mac Studio's local model
    # server ("flash-next", ds4-server on 127.0.0.1:8000, served tailnet-wide
    # via `tailscale serve` at http://mac-studio.tailc8cc93.ts.net:8000) is
    # reachable from his other devices, starting with a test from his MacBook
    # (joes-macbook-pro.tailc8cc93.ts.net). host_allowlisted does suffix
    # matching, so this one entry covers every device name on HIS tailnet, and
    # SSH between his own Macs. Deliberately scoped to this one tailnet, not
    # the broad `ts.net` suffix: someone else's tailnet must stay blocked.
    "tailc8cc93.ts.net",
    # census.gov: the U.S. Census Bureau's own federal domain. Added 2026-09-25
    # on Joe's explicit in-chat approval to download two public files for the
    # J302 Safe Harbor census tables behind the heat-map privacy builder: the
    # 2020 county reference file and the 2020 DHC ZCTA population. The guard
    # was refusing both www2.census.gov and api.census.gov.
    #
    # SCOPE, stated because an allowlist entry is a standing permission: this is
    # read-only public statistical data from one federal owner, the same trust
    # class as download.cms.gov and alabama.gov above. It is the whole domain
    # rather than the two hosts because the job needs two subdomains of the same
    # owner and the Bureau spreads one dataset across several of them. The
    # anchored suffix match covers census.gov and *.census.gov only: lookalikes
    # such as census.gov.<other> and notcensus.gov stay blocked, and
    # ops/guard-selftest.py asserts both.
    "census.gov",
)

# ── render-write protection over Bash (2026-08-06, Joe: "Fix both now") ──────
# The record-home gate denies Edit/Write on generated renders, but an ordinary
# shell redirect walked around it: the #214 audit proved `echo >> open-loops.md`
# ALLOWED while Edit on the same file was DENIED. This closes the second door.
# The protected-path list is record-home-gate's own (parsed live from
# exporters/targets.py) — one list, two doors. BEST-EFFORT PARSER, stated
# plainly: it catches the ordinary write shapes the audit demonstrated
# (>, >>, tee, cp/mv/rsync targets, sed -i, truncate, python open(...,'w')).
# It does not chase adversarial obfuscation; the gate degrades open on its own
# errors like the rest of this file.

def _vault_spellings():
    """The primary machine's two literal spellings, PLUS whatever vault actually
    exists here. Additive on purpose (2026-08-10 audit): the hardcodedは
    pair is kept verbatim so this machine's matching cannot regress, while a second
    machine stops running a vault guard that matches no path it owns."""
    home = os.path.expanduser("~")
    fixed = (
        home + "/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/",
        home + "/My Drive/CARR AI/",
    )
    try:
        from gate_paths import vault_roots
        return tuple(dict.fromkeys(fixed + vault_roots()))
    except Exception:
        return fixed          # degrade open, like the rest of this file


_VAULT_SPELLINGS = _vault_spellings()

_render_paths_cache = None


def _protected_abs_paths():
    """Absolute protected render paths under BOTH vault spellings, from
    record-home-gate's generated_paths(). Cached per invocation; [] on any
    error (fail open, logged by caller)."""
    global _render_paths_cache
    if _render_paths_cache is not None:
        return _render_paths_cache
    try:
        import importlib.util
        gate_py = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "record-home-gate.py")
        spec = importlib.util.spec_from_file_location("_rhg", gate_py)
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        exact, dirs = g.generated_paths()
        paths = []
        for rel in list(exact):
            for v in _VAULT_SPELLINGS:
                paths.append(v + rel)
        _render_paths_cache = (paths, [v + d.rstrip("/") + "/" for d in dirs for v in _VAULT_SPELLINGS])
    except Exception:
        _render_paths_cache = ([], [])
    return _render_paths_cache


# sed -i: any protected path inside a `sed -i` clause IS the in-place write
# target, so the pattern accepts anything between -i and the path short of a
# clause separator (macOS sed carries a backup-suffix arg the first version
# of this regex missed).
_WRITE_BEFORE_CTX = re.compile(
    r"(>>?|\btee(\s+-a)?|\bsed\s+-i[^|;&]*|\btruncate\b[^|;&]*)\s*[\"']?$")


def render_write_target(cmd):
    """Reason string when the command writes onto a protected render, else None."""
    exact, gen_dirs = _protected_abs_paths()
    if not exact and not gen_dirs:
        return None
    hits = [p for p in exact if p in cmd]
    hits += [d for d in gen_dirs if d in cmd]
    if not hits:
        return None
    for p in hits:
        idx = 0
        while True:
            idx = cmd.find(p, idx)
            if idx < 0:
                break
            before = cmd[max(0, idx - 60):idx]
            if _WRITE_BEFORE_CTX.search(before):
                return (f"write onto a generated render via shell ({os.path.basename(p.rstrip('/'))}) — "
                        f"renders are written by the exporter only; use the record verb instead "
                        f"(blocked by the CARR guard, second door of record-home-gate)")
            idx += len(p)
    # cp/mv/rsync: protected path as the DESTINATION (last path argument of the clause)
    for clause in re.split(r"[;&|]", cmd):
        toks = clause.strip().split()
        if not toks:
            continue
        if toks[0] in ("cp", "mv", "rsync"):
            tail = clause.strip()
            for p in hits:
                if tail.rstrip("\"' ").endswith(p.rstrip("/")):
                    return (f"{toks[0]} onto a generated render ({os.path.basename(p.rstrip('/'))}) — "
                            f"renders are written by the exporter only; use the record verb instead "
                            f"(blocked by the CARR guard, second door of record-home-gate)")
    # python inline write onto a protected path
    for p in hits:
        if re.search(r"open\(\s*[\"']" + re.escape(p) + r"[\"']\s*,\s*[\"'][wa]", cmd):
            return (f"python write onto a generated render ({os.path.basename(p)}) — "
                    f"use the record verb instead (blocked by the CARR guard)")
    return None

# Vault .md paths in a shell command: vault spelling + relative tail up to a
# shell metachar/quote. Case-insensitive match would be wrong (APFS is
# case-insensitive but the manifest compares normalized real paths; the Write
# hook is the primary door — this door catches the shell walk-around).
_VAULT_MD_RE = None


def vault_md_write_target(cmd):
    """PHASE 0 (doctrine-store build, decision 82a2fb62): the Bash door of the
    vault-wide .md deny-by-default. Same manifest as record-home-gate — one
    module, hooks/md_manifest.py — so the two doors cannot drift (rule a8c55a47).
    Renders are caught earlier by render_write_target with a sharper message."""
    global _VAULT_MD_RE
    try:
        if _VAULT_MD_RE is None:
            alts = "|".join(re.escape(v) for v in _VAULT_SPELLINGS)
            _VAULT_MD_RE = re.compile(r"(" + alts + r")([^\"'|;&<>]+?\.md)\b")
        hits = [(m.start(), m.group(2)) for m in _VAULT_MD_RE.finditer(cmd)]
        if not hits:
            return None
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from md_manifest import md_write_verdict
        for idx, rel in hits:
            verdict = md_write_verdict(rel.strip())
            if not verdict:
                continue
            before = cmd[max(0, idx - 60):idx]
            written = bool(_WRITE_BEFORE_CTX.search(before))
            if not written:
                # cp/mv/rsync destination, or a python open(..., "w"/"a")
                for clause in re.split(r"[;&|]", cmd):
                    toks = clause.strip().split()
                    if toks and toks[0] in ("cp", "mv", "rsync") \
                            and clause.rstrip("\"' ").endswith(rel):
                        written = True
                        break
                if re.search(r"open\(\s*[\"'][^\"']*" + re.escape(rel) + r"[\"']\s*,\s*[\"'][wa]",
                             cmd):
                    written = True
            if written:
                return (f"shell write onto vault markdown — {verdict} "
                        f"(blocked by the CARR guard, Bash door of the Phase 0 .md write-block)")
        return None
    except Exception:
        return None                                   # fail open, same as siblings


def corpus_render_write_target(cmd):
    """The Bash door of the corpus-render write-block (2026-08-10, Joe's
    instruction after the nightly chain failed on eight Drive-side edits).
    Same deny set as the Write/Edit door — one module, hooks/corpus_renders.py —
    so the two doors cannot drift apart (rule a8c55a47: a manual path and an
    automated path that do the same job must be the same code).

    `corpus-sync.py` itself is exempt and must be: it is the sanctioned writer
    of every one of these files. It never names them literally, so the exemption
    is belt-and-braces rather than load-bearing."""
    try:
        if "corpus-sync.py" in cmd:
            return None
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from corpus_renders import paths_in_command, verdict
        hits = paths_in_command(cmd)
        if not hits:
            return None
        for spelling, real in hits.items():
            written = False
            idx = 0
            while True:
                idx = cmd.find(spelling, idx)
                if idx < 0:
                    break
                if _WRITE_BEFORE_CTX.search(cmd[max(0, idx - 60):idx]):
                    written = True
                    break
                idx += len(spelling)
            if not written:
                for clause in re.split(r"[;&|]", cmd):
                    toks = clause.strip().split()
                    if toks and toks[0] in ("cp", "mv", "rsync") \
                            and clause.rstrip("\"' ").endswith(spelling):
                        written = True
                        break
            if not written and re.search(
                    r"open\(\s*[\"'][^\"']*" + re.escape(os.path.basename(spelling))
                    + r"[\"']\s*,\s*[\"'][wa]", cmd):
                written = True
            if written:
                return (f"shell write onto a corpus render — {verdict(real, 'this command')} "
                        f"(blocked by the CARR guard, Bash door of the corpus-render block)")
        return None
    except Exception:
        return None                                   # fail open, same as siblings


# WIDENED 2026-08-14 BY A SWEEP, after the raw-device rule was found missing the
# ordinary spelling of its own command while somebody picked an example for an
# unrelated fixture. Probing every rule against the real ways its command is
# written turned up eight misses across four rules — and none of it had ever
# surfaced, because no fixture asserted these rules FIRE. The suite tested the
# allow side and the network side and took the destructive side on trust. A rule
# with no fire-asserting test is indistinguishable from a rule that does not
# work. ops/guard-selftest.py now pins each one.
#
# DEVICE FAMILIES STAY ENUMERATED, never a bare /dev/ match: /dev/null,
# /dev/tty and /dev/stderr are everyday redirection targets, and refusing those
# would break ordinary work within the hour.
_DEV = r"/dev/(sd|disk|rdisk|nvme|vd|hd)"

RULES = [
    # 1. destructive filesystem
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+", re.I), "recursive/forced delete"),
    # `find ... -delete` removes exactly what rm -rf removes, one directory walk
    # at a time, and was the only whole-tree delete this table did not see. It
    # keeps the same safe-zone waiver as rm, so the routine scratch cleanup
    # (`find <scratch> -name '*.pyc' -delete`) is unaffected.
    (re.compile(r"\bfind\b[^|;&]*\s-delete\b", re.I), "recursive/forced delete"),
    (re.compile(r"\b(shred|srm)\b", re.I), "secure delete"),
    # Three ways to write a raw device, not one. The original pattern saw only
    # shell redirection, so `dd of=<device>` — the form anyone actually reaches
    # for, and the one in every disk-imaging guide — went straight through.
    (re.compile(r">\s*" + _DEV, re.I), "raw device write"),
    (re.compile(r"\bdd\b[^|;&]*\bof=" + _DEV, re.I), "raw device write"),
    (re.compile(r"\btee\b[^|;&]*\s" + _DEV, re.I), "raw device write"),
    # newfs_* is the macOS spelling of mkfs, and partitionDisk destroys just as
    # completely as eraseDisk while reading like a layout change.
    (re.compile(r"\bmkfs\b|\bnewfs_\w+\b|\bdiskutil\s+(erase|partitionDisk|reformat)", re.I),
     "filesystem format"),
    # 2. git history rewrite
    # A leading + on a refspec IS a force push — the same overwrite with none of
    # the flags this rule was watching for.
    (re.compile(r"git\s+push\b[^|;&]*(--force\b|--force-with-lease\b|\s-f\b|\s\+\S+:)", re.I),
     "force push"),
    (re.compile(r"git\s+reset\s+--hard\b", re.I), "hard reset"),
    (re.compile(r"git\s+(filter-repo|filter-branch)\b", re.I), "history rewrite"),
    (re.compile(r"git\s+clean\s+-[a-zA-Z]*f", re.I), "forced clean"),
    # 2b. `--no-verify` / core.hooksPath REDESIGNED OUT (2026-09-24, Opus
    # review, bypass audit C38). These were shell-text regexes over a
    # locally-run accident-stopper hook (ops/githooks/pre-commit itself says
    # so: "not a security control: anyone can bypass it with --no-verify").
    # Jev agreed (0.94) that hosted CI, not this local hook, is what actually
    # stops a bypass from reaching main — so this file no longer chases every
    # spelling of a local-only escape hatch. Broad add is different: it is
    # covered below as a dedicated, argument-aware function rather than a
    # regex, because AGENTS.md makes a specific, checkable promise about it
    # (see broad_add_reason()).
    # 3. private key material
    #
    # `\.age\b` REMOVED 2026-08-07, on Joe's ruling: "loosen the gate so the work
    # can actually be done." The extension matched every ENCRYPTED BACKUP in
    # backups/, which is ciphertext, not key material — without the identity it
    # is noise. What it actually blocked was ordinary custodial work on the
    # backups: listing them, comparing sizes, quarantining a corrupt one into
    # _to_delete/, naming an R2 object key. It fired twice on 2026-08-07 against
    # correct operations during the recovery from a corrupt backup — including
    # the attempt to move that corrupt backup out of restore range. A guard that
    # blocks the cleanup of the incident it was watching is costing more than it
    # protects.
    #
    # The private key itself is still covered, by name and by path: it lives at
    # ~/.config/carr/age-key.txt, which `age-key` matches, with `identity.txt`
    # behind it for the --identity override.
    (re.compile(r"(id_rsa|id_ed25519|\.ssh/|age-key|identity\.txt|\.pem\b|\.p12\b)", re.I),
     "private key material"),
    # 3b. THE REAL EXPOSURE THE EXTENSION WAS STANDING IN FOR, now named directly.
    # Handling a .age file is harmless; DECRYPTING one spills production PII in
    # cleartext. So the dangerous verb is blocked instead of the file type, which
    # is both tighter and less obstructive than what it replaces.
    # bin/restore-rehearse.sh is unaffected: it decrypts INSIDE the script, into a
    # mode-700 mktemp outside the repo, and shreds it on every exit path including
    # a Ctrl-C. The gate sees `./run.sh restore-rehearse ...`, never the age call.
    # LIMIT, stated rather than pretended away: this matches the flags real usage
    # spells out (-d, --decrypt, -i, --identity). It does not chase bundled short
    # flags or obfuscation, same as every other rule here.
    (re.compile(r"\bage\s+[^|;&]*(--?d\b|--decrypt\b|--?i\b|--identity\b)", re.I),
     "raw decrypt of an encrypted backup — production data in cleartext. Use "
     "`./run.sh restore-rehearse --verify-only [--date YYYYMMDD]`, which handles the "
     "key and shreds the plaintext"),
    # 4. destructive SQL
    (re.compile(r"\bdrop\s+(table|schema|database|view|index)\b", re.I), "DROP"),
    (re.compile(r"\btruncate\s+(table\s+)?\w", re.I), "TRUNCATE"),
    (re.compile(r'\bdelete\s+from\s+(?:[\w".]+)\s*(;|$)', re.I), "unqualified DELETE"),
    (re.compile(r'\bupdate\s+[\w".]+\s+set\b(?![^;]*\bwhere\b)', re.I), "unqualified UPDATE"),
]

# ── IS THIS COMMAND ACTUALLY SENDING? (loop #283, fixed 2026-08-13) ───────────
#
# THE BUG THIS REPLACES, and it was a false positive that blocked real work every
# time it fired. The old test was:
#
#     SEND_CTX = re.compile(r"\b(curl|wget|http|https)\b", re.I)
#
# `http` and `https` as bare words appear inside EVERY quoted URL, so any command
# that merely MENTIONED a link was treated as a network send and then had to pass
# every host in it through the allowlist. Found 2026-08-09 by the
# linkedin-engagement-daily run: it assembled its own deliverable — open a local
# file, concatenate strings, write it back — and was refused with "network send to
# an unrecognised host (www.linkedin.com)". No network call was made or attempted.
# That routine's own SOP mandates a permalink per item, so every run hit it, and
# the x-reply run stores post links the same way.
#
# WHY NOT JUST ALLOWLIST linkedin.com. Because that widens REAL egress to fix a
# case where no egress happens, and leaves every other URL-quoting file write
# still broken. The loop names the right fix and this is it: decide from the
# command's EXECUTABLE first, and only then look at hostnames.
#
# THE SENDER TEST. A sender name counts only in COMMAND POSITION — at the start of
# the command, or after a pipe, semicolon, `&&`, `||`, a subshell opener, or one
# of the usual prefix words (sudo/env/xargs/time/nohup) — optionally with a
# leading path. That is what keeps `https` in `"see https://example.com"` from
# matching while `curl`, `/usr/bin/curl` and `... | xargs curl` all still do.
# `http`/`https` REMAIN senders in command position, because httpie's client is
# literally named `http` and dropping it would open a real hole.
SENDER = (r"curl|wget|nc|ncat|netcat|telnet|ftp|sftp|scp|rsync|ssh|httpie|http|https"
          r"|links|lynx|w3m|aria2c|axel|fetch")
SEND_CTX = re.compile(
    r"(?:^|[|;&(){}`\n]|\$\(|&&|\|\||\bsudo\b|\bxargs\b|\benv\b|\btime\b|\bnohup\b|\bdoas\b)"
    r"\s*(?:[\w./-]*/)?(?:" + SENDER + r")\b",
    re.I)

# AN INTERPRETER THAT IMPORTS A NETWORK CLIENT IS ALSO A SENDER, and this half is
# what keeps the narrower executable test from becoming a hole: `python3 -c "import
# requests; requests.post(...)"` has no sender in command position. Matching the
# library reference catches it. A `python3 -c` that only opens a local file and
# writes a string — the exact shape loop #283 was about — matches neither and is
# correctly allowed through to the write-target checks below.
NET_CLIENT = re.compile(
    r"\b(?:urllib|urlopen|requests\.|httpx|http\.client|httplib|aiohttp|websockets?"
    r"|net/http|Net::HTTP|LWP|WWW::Mechanize|axios|node-fetch|XMLHttpRequest"
    r"|socket\.(?:socket|create_connection)|fetch\()",
    re.I)
URL_RE = re.compile(r"https?://([A-Za-z0-9._-]+)")

# A REMOTE COPY TARGET IS A HOST TOO, and this was a real gap rather than a
# consequence of the loop #283 change — URL_RE has only ever understood `http://`,
# so `scp db.sql user@host:/tmp` named no host, passed the allowlist by having
# nothing to check, and was allowed. Found 2026-08-13 by writing
# ops/egress-scope-selftest.py, which asserted the property the guard was assumed
# to have; the assertion failed and the assumption was wrong.
#
# Deliberately narrow: it matches the `user@host:` form that scp and rsync use for
# a remote path. It is only ever consulted when a sender is already in command
# position, so an email address quoted in a git commit message reaches it never.
REMOTE_TARGET_RE = re.compile(r"[\w.+-]+@([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}):")

# ── IS THIS COMMAND ACTUALLY RUNNING SQL? (loop #240, 2026-08-13) ─────────────
#
# SAME DEFECT AS THE EGRESS ONE FIXED EARLIER THE SAME DAY, in the other rule set,
# and found the same way — by the guard blocking real work. The destructive-SQL
# patterns scan the whole command string, so `\btruncate\s+(table\s+)?\w` matched
# the ENGLISH PHRASE "truncate honestly" inside a loop's closing outcome, and the
# session could not file it. No SQL was involved anywhere in that command.
#
# The patterns themselves are right and are NOT loosened: a real TRUNCATE, DROP,
# unqualified DELETE or unqualified UPDATE stays blocked. What changes is that they
# are consulted only when the command plausibly EXECUTES SQL at all. Prose that
# quotes a SQL keyword is not a database operation, exactly as prose that quotes a
# URL is not a network send.
#
# Deliberately generous about what counts as SQL context: a false positive here
# costs a blocked session, a false negative costs a table. Anything that opens a
# database, names a .sql file, or executes a statement string qualifies.
SQL_CONTEXT = re.compile(
    r"(?:\bpsql\b|\bpg_dump\b|\bpgcli\b|\bmysql\b|\bsqlite3?\b|\bdb-tap\b|\bmigrate\b"
    r"|\.sql\b|\bDATABASE_URL\b|\bCARR_DB_[A-Z_]+\b|\bneonctl\b"
    r"|\bcur\.execute\b|\bconn\.execute\b|\bcursor\(\)|\bexecute_many\b"
    r"|\bpsycopg\b|\bsqlalchemy\b|--command\b|\s-c\s+['\"])",
    re.I)

# The labels above that are ONLY meaningful against a database. Kept as an explicit
# set rather than inferred from the pattern, so adding a non-SQL rule later cannot
# accidentally inherit the SQL gate.
SQL_LABELS = frozenset({"DROP", "TRUNCATE", "unqualified DELETE", "unqualified UPDATE"})

# DESCRIBING A DESTRUCTIVE COMMAND IS NOT RUNNING ONE — the same carve-out this
# guard already makes for SQL keywords in prose (loop #240), applied to the
# command-shaped rules. The patterns stay exactly as strict; they are simply
# consulted against the part of the command the shell will actually EXECUTE,
# because a quoted --body and a heredoc body are handed to a program as bytes.
#
# Measured on 2026-08-14, three times in one session: a pull-request body
# reporting a fix to the writer gate, a comment verifying that fix had landed,
# and a probe command investigating this very guard were each refused for
# naming the command they were about. The workaround every time was moving the
# text into a file, which is precisely how a gate teaches people to route
# around it — and a gate people route around has already stopped working.
#
# THE CATASTROPHIC LABELS ARE DELIBERATELY ABSENT from this set: filesystem
# format, raw device write and the two private-key rules keep no carve-out at
# all. A wiped disk or a leaked key is unrecoverable, those phrases are
# vanishingly rare in honest prose, and the cost of a false refusal there is a
# rephrase rather than a restore. Conservatism belongs at the extremes.
PROSE_SAFE_LABELS = frozenset({
    "force push", "hard reset", "history rewrite", "forced clean",
    "recursive/forced delete", "secure delete",
})


def is_sql_context(cmd):
    """True when the command could actually reach a database."""
    return bool(SQL_CONTEXT.search(cmd))


def hosts_in(cmd):
    """Every host this command could reach: URL hosts plus remote-copy targets."""
    hosts = []
    from cmd_text import shell_tokens
    try:
        tokens = shell_tokens(cmd)
    except ValueError:
        tokens = re.split(r'[\s;&|]', cmd)
    for token in tokens:
        for url in re.findall(r'https?://[^\s\'"<>]+', token, re.I):
            try:
                hosts.append(urlsplit(url).hostname or "invalid-url")
            except ValueError:
                hosts.append("invalid-url")
    return hosts + REMOTE_TARGET_RE.findall(cmd)


def is_send_context(cmd):
    """True when the command can actually put bytes on the network.

    Two independent tests, either sufficient: a sending EXECUTABLE in command
    position, or an interpreter that references a network client library. A
    command that merely quotes a URL matches neither.
    """
    return bool(SEND_CTX.search(cmd) or NET_CLIENT.search(cmd))

# ── THE DERIVED HOST LIST (2026-08-09, the "B" half of Joe's "build A and B") ─
#
# KNOWN_HOSTS above is trust as CODE, which is right for infrastructure and wrong
# for client practice websites: a different host per client, unknown until the
# client exists, growing weekly. Encoding those in code means one hand-edit per
# client forever, each one a session asking to widen a security allowlist.
#
# So the rest of the list is DATA, generated by ops/fetch-allowlist.py from the
# email domains of CLIENTS AND LEADS already in the record — partner-entered at
# intake, free-mail filtered. Read that file's header for the full reasoning,
# including why it is email domains rather than a website column, and the honest
# limit (this makes widening AUDITED, not impossible: a verb can write an email).
#
# FAILS OPEN TO THE NARROW LIST, never to a wider one: if the file is missing or
# unreadable, the result is KNOWN_HOSTS alone. A generator that breaks tightens
# the gate rather than loosening it, which is the correct direction for a
# security control to fail.
_DERIVED_CACHE = {"mtime": None, "hosts": ()}
DERIVED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "out", "fetch-allowlist.txt")


def derived_hosts():
    """Hosts from the record-derived list. Cached on mtime; never raises."""
    try:
        mt = os.path.getmtime(DERIVED_PATH)
    except OSError:
        return ()
    if _DERIVED_CACHE["mtime"] == mt:
        return _DERIVED_CACHE["hosts"]
    try:
        with open(DERIVED_PATH, encoding="utf-8") as fh:
            hosts = tuple(ln.strip().lower() for ln in fh
                          if ln.strip() and not ln.lstrip().startswith("#"))
    except Exception:
        return ()
    _DERIVED_CACHE.update(mtime=mt, hosts=hosts)
    return hosts


def host_allowlisted(host):
    """True if host is on the code list OR the record-derived list."""
    host = (host or "").strip().lower()
    normalized = host.strip(".")
    # The app host is a single allowed origin. DNS permits one terminal dot;
    # retaining the raw host here rejects extra dots and subdomains.
    if normalized == "app.doctorcre.com":
        return host in ("app.doctorcre.com", "app.doctorcre.com.")
    if normalized.endswith(".app.doctorcre.com"):
        return False
    host = normalized
    if not host:
        return False
    for k in KNOWN_HOSTS:
        if host == k or host.endswith("." + k):
            return True
    for k in derived_hosts():
        if host == k or host.endswith("." + k):
            return True
    return False


# ── THE WEBFETCH READ-ONLY CLASS (2026-08-09, Joe: "build A and B") ──────────
#
# THE ERROR THIS FIXES. Until now WebFetch and Bash `curl` shared ONE allowlist,
# and that forced the list to be as tight as the riskiest caller. Their
# exfiltration ceilings are not remotely comparable:
#
#     WebFetch   GET only · payload out = the URL string · no credentials ·
#                cross-host redirects are RETURNED to the caller, not followed
#                (observed live twice on 2026-08-07 against alabamainteractive)
#     Bash curl  any method · any body · credentials · follows redirects
#
# The guard's own stated job (see the header, class 4) is the EXFILTRATION
# guard. A GET whose entire outbound payload is a URL is a different risk from a
# POST that can carry a database, so it gets a different policy. KNOWN_HOSTS is
# untouched and still governs Bash.
#
# WHAT THIS BUYS. Client verification needs the practice's own website, and
# practice websites cannot be enumerated — there is a different one per client,
# unknown until the client exists. An allowlist can never close that; a policy
# can. This is what makes the weekly deal-history research slice able to read a
# practice site at all.
#
# THE EXFILTRATION CONTROL IS THE LENGTH CAP, not the host list. If a GET can
# only leave with what fits in its URL, then capping the URL caps the channel.
# 256 chars total with a near-empty query admits ordinary pages and refuses a
# payload. Everything else here is the SSRF floor.
#
# KNOWN LIMIT, STATED PLAINLY: a hostname that RESOLVES to a private address
# (DNS rebinding) is not caught. Resolving in a PreToolUse hook would add a
# network round-trip to every call and is itself resolver-dependent. The bare-IP
# and private-suffix rules below stop the direct forms; rebinding is accepted
# residual risk on a single-operator machine, and is logged here so the next
# reader does not mistake its absence for an oversight.
FETCH_MAX_URL = 256          # total characters
FETCH_MAX_QUERY = 64         # characters after '?'

_FETCH_HOST_DENY_SUFFIX = (
    "localhost", "local", "internal", "localdomain", "home.arpa", "lan",
)
# Anything that looks like a secret riding in the query string.
_FETCH_SECRETISH = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential|authorization|"
    r"session[_-]?id|access[_-]?key)\b")
# A long unbroken high-entropy run — a base64/hex blob being smuggled out.
_FETCH_BLOB = re.compile(r"[A-Za-z0-9+/_=-]{40,}")


def webfetch_open_read_reason(url):
    """None if this GET is acceptable under the wider read-only class, else why not.

    Only consulted for hosts NOT already in KNOWN_HOSTS — an allowlisted host
    keeps its existing unconditional pass, so nothing that worked before breaks.
    """
    try:
        parts = urlsplit(url if url.startswith(("http://", "https://")) else f"https://{url}")
    except Exception as exc:                       # unparseable → refuse
        return f"WebFetch URL could not be parsed ({type(exc).__name__}) — blocked by the CARR guard."

    if len(url) > FETCH_MAX_URL:
        return (f"WebFetch URL is {len(url)} chars (cap {FETCH_MAX_URL}) for a host outside "
                f"KNOWN_HOSTS — blocked by the CARR guard. A long URL to an unvetted host is "
                f"the exfiltration channel this cap exists to close.")
    if len(parts.query) > FETCH_MAX_QUERY:
        return (f"WebFetch query string is {len(parts.query)} chars (cap {FETCH_MAX_QUERY}) for a "
                f"host outside KNOWN_HOSTS — blocked by the CARR guard.")
    if parts.username or parts.password or "@" in parts.netloc:
        return "WebFetch URL carries credentials in the authority — blocked by the CARR guard."
    if _FETCH_SECRETISH.search(parts.query) or _FETCH_BLOB.search(parts.query):
        return ("WebFetch query string looks like it carries a secret or an encoded blob — "
                "blocked by the CARR guard.")

    host = (parts.hostname or "").strip(".").lower()
    if not host:
        return "WebFetch URL has no host — blocked by the CARR guard."

    try:
        port = parts.port
    except ValueError:
        return "WebFetch URL has a malformed port — blocked by the CARR guard."
    if port not in (None, 80, 443):
        return (f"WebFetch to a non-standard port ({port}) on a host outside KNOWN_HOSTS — "
                f"blocked by the CARR guard.")

    # A bare IP literal skips DNS entirely and is how link-local metadata
    # (169.254.169.254) and RFC1918 targets get reached. The wider class is for
    # named public websites, so require a name.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return (f"WebFetch to a bare IP address ({host}) outside KNOWN_HOSTS — blocked by the "
                f"CARR guard. The open read class requires a public DNS name.")

    last = host.rsplit(".", 1)[-1]
    if last in _FETCH_HOST_DENY_SUFFIX or host in _FETCH_HOST_DENY_SUFFIX:
        return f"WebFetch to a non-public host ({host}) — blocked by the CARR guard."
    if "." not in host:
        return (f"WebFetch to a dotless host ({host}) — blocked by the CARR guard; that is a "
                f"local name, not a public website.")
    return None


def log(msg):
    """Timestamped and self-identifying. Before 2026-08-03 no hook stamped its
    lines, so out/hook-guard.log could not answer "when did this fire" or even
    "which gate wrote this" — 51 lines with test fixtures indistinguishable from
    production denials. A log you cannot read chronologically is an artifact,
    not a check."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} guard-unattended {msg.rstrip()}\n")
    except Exception:
        pass


def in_safe_zone(cmd):
    from cmd_text import shell_tokens, shell_operands, SHELL_BOUNDARIES
    try:
        tokens, _ = shell_operands(shell_tokens(cmd))
    except ValueError:
        return False
    targets = []
    segments, segment = [], []
    for token in tokens + [';']:
        if token in SHELL_BOUNDARIES:
            if segment:
                segments.append(segment)
            segment = []
        else:
            segment.append(token)
    for words in segments:
        while words and (words[0] in {'sudo', 'command', 'env'} or re.match(r'^\w+=', words[0])):
            words = words[1:]
        if not words:
            continue
        executable = os.path.basename(words[0])
        if executable in {'rm', 'srm'}:
            targets.extend(token for token in words[1:] if not token.startswith('-'))
        elif executable == 'find' and '-delete' in words:
            if any(token in {'-exec', '-execdir', '-ok', '-okdir'} for token in words):
                return False  # executable predicates cannot establish a safe cleanup
            roots = []
            for token in words[1:]:
                if token.startswith('-') or token in {'(', '!', ')'}:
                    break
                roots.append(token)
            if not roots:
                return False  # find defaults to an unbound working directory
            targets.extend(roots)
    def safe_target(target):
        if '$' in target or '`' in target or '..' in target.split('/'):
            return False
        parts = target.split("/")
        for zone in SAFE_ZONES:
            if zone.startswith("/"):
                if target.startswith(zone):
                    return True
            else:
                zone_parts = zone.rstrip("/").split("/")
                if any(parts[i:i + len(zone_parts)] == zone_parts
                       for i in range(len(parts))):
                    return True
        return False
    return bool(targets) and all(safe_target(target) for target in targets)


# ── Rebasing your own branch in place ────────────────────────────────────────
#
# WHY THIS CARVE-OUT EXISTS (measured 2026-08-15). The "force push" rule above is
# blanket: it cannot see WHERE the push lands, so it refused a force-push at a
# session's own feature branch exactly as hard as one aimed at main. main takes
# roughly a hundred commits a day here, so a branch going stale mid-review is the
# normal case — and the standard repair, `git rebase origin/main` then force-push
# the SAME branch, was unavailable. The only route left was to abandon the branch,
# cut a new one and open a second pull request.
#
# That is a corpse factory, and it is not a theory: four closed pull requests say
# it in their own closing notes, in the same words — "reopened on a fresh branch
# because rewriting the pushed one needs a force-push, which the unattended guard
# blocks" (#125, #131, #142, #100). GitHub's auto-delete fires on MERGE and never
# on close, so every one of those left a permanent orphan. 25 closed pull requests
# and 35 dead branches were swept by hand on 2026-08-15. The guard was
# manufacturing the mess, and no amount of sweeping addresses that.
#
# WHAT IS DELIBERATELY *NOT* WIDENED, because the point is to remove a
# manufacturing defect rather than to hand back the whole capability:
#
#   main and master           — still refused. The server ruleset also forbids it,
#                               and belt-and-braces is right for the one ref whose
#                               history everything else is measured against.
#   bare --force / -f         — still refused even at a feature branch. Lease is
#                               the whole safety story: it aborts when the remote
#                               moved under you, which is the only thing standing
#                               between a rebase and a peer session's pushed work.
#                               Bare force has no such check and many sessions
#                               share this machine.
#   +refspec                  — still refused; same overwrite, no lease.
#   no named target           — still refused. Without an explicit ref the push
#                               takes the current branch, which the guard cannot
#                               see and which may be main. Naming it is the same
#                               house rule as committing by named paths, and it
#                               makes this decision auditable after the fact.
PROTECTED_REFS = frozenset({"main", "master", "HEAD"})
_LEASE = re.compile(r"--force-with-lease(?:=\S*)?\b", re.I)
# `--force\b` also matches the "--force" inside "--force-with-lease", which is
# what makes the blanket rule catch the lease form at all. Detecting the BARE
# spelling therefore needs an explicit "not followed by -with-lease".
_BARE_FORCE = re.compile(r"--force(?!-with-lease)\b|\s-f\b|\s\+\S+:", re.I)
_GIT_PUSH = re.compile(r"git\s+push\b", re.I)
# Where one command ends. `&&`, `||`, `;` and a bare `|` end it; a REDIRECTION
# ends its argument list too, and must be recognised separately because `2>&1`
# carries an ampersand that is not a separator — the exact thing that defeated
# the previous version of this parser.
_SEPARATOR = re.compile(r"^(?:&&|\|\||[;|&])$")
_REDIRECT = re.compile(r"^\d*(?:>>?|<)&?\d*$|^[<>]")


def force_push_to_named_side_branch(cmd):
    """True only for the one safe shape: --force-with-lease at a named non-main ref.

    Reads the EXECUTABLE remainder, not the raw command, so a feature-branch
    example quoted in a pull-request body cannot vouch for a real force-push at
    main sitting beside it in the same line.
    """
    text = strip_inert_text(cmd)
    if len(_GIT_PUSH.findall(text)) != 1:
        return False          # two pushes in one line: cannot reason, so refuse
    if not _LEASE.search(text) or _BARE_FORCE.search(text):
        return False
    # ANCHOR ON THE REAL `git push`, NOT ON THE SUBSTRING "push".
    #
    # This read `text.split("push", 1)[1]` when the carve-out first shipped, and
    # it was wrong in the shape every session actually sends. The guard receives
    # the WHOLE command line, and a session working in a worktree always writes
    # `cd <path> && git push …`. Any earlier "push" — in the directory, in the
    # branch name — consumed the split, so the refspec parsed as nonsense and the
    # push was refused. It failed on the very first real use: the branch was
    # `force-push-narrow`, so its own worktree path contained "push".
    match = _GIT_PUSH.search(text)
    if not match:
        return False
    # READ TOKENS UNTIL THIS COMMAND ENDS, rather than cutting the string on raw
    # separator CHARACTERS.
    #
    # The character cut was `re.split(r"[;|&]", …)`, and it broke on `2>&1` — the
    # redirection every one of these commands carries in practice, whose ampersand
    # is not a separator at all. It chopped mid-redirect, left a stray `2>` in the
    # argument list, counted three words instead of two, and refused the push. The
    # first two versions of this parser were each defeated by a piece of perfectly
    # ordinary shell syntax the test cases had quietly excluded, so it now walks
    # tokens and stops at a real boundary.
    #
    # Stopping at the boundary is what keeps this honest: only THIS command's
    # arguments are read, so nothing chained after it can dress up its target.
    words = []
    # shlex separates an IO number from its operator (2, >&, 1). Remove only
    # unquoted numbers adjacent to a redirect; `2 >` and `'2'>` are arguments.
    remainder = re.sub(
        r"'[^']*'|\"(?:\\.|[^\"\\])*\"|\\.|(?<!\S)\d+(?=[<>])",
        lambda m: "" if m.group(0).isdigit() else m.group(0), text[match.end():])
    try:
        lexer = shlex.shlex(remainder, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    for token in tokens:
        if _SEPARATOR.match(token) or _REDIRECT.match(token):
            break             # this command's arguments end here
        if token.startswith("-"):
            continue          # a flag, not a destination
        words.append(token)
    if len(words) != 2:
        return False          # no remote+ref pair means no visible destination
    ref = words[1].split(":")[-1]
    ref = re.sub(r"^refs/heads/", "", ref)
    return bool(ref) and ref not in PROTECTED_REFS


def raw_targets_carr(cmd):
    """Recognize CARR path spellings without evaluating shell variables."""
    if not isinstance(cmd, str):
        return False
    markers = (
        r"(?:^|[^\w])~/carr-system(?:/|\b)",
        r"\$(?:\{HOME\}|HOME)/carr-system(?:/|\b)",
        r"/Users/[^/]+/carr-system(?:/|\b)",
        r"(?:CloudStorage/GoogleDrive-[^/]+/)?My\\? Drive/CARR\\? AI(?:/|\b)",
        r"/CARR\\? AI(?:/|\b)",
    )
    return any(re.search(marker, cmd) for marker in markers)


def delegation_control_plane_write(cmd):
    """Block ordinary shell tampering with the delegation gate's mutable state.

    The hook itself updates this state by direct atomic filesystem calls, not by
    issuing a harness Bash tool call, so this does not interfere with normal
    state transitions.  A model-issued shell command, however, must never edit
    the latch/control files to release its own task.
    """
    names = (DELEGATION_STATE, DELEGATION_LOCK, "delegation-gate-state.json",
             ".delegation-gate-")
    if not any(name in cmd for name in names):
        return None
    write_shape = re.compile(r"(>>?|\btee\b|\bsed\s+-i\b|\btruncate\b|\brm\b|\bmv\b|\bcp\b|\bchmod\b|\bchflags\b|open\([^\n]+['\"][wa])", re.I)
    if write_shape.search(cmd):
        return "delegation control-plane state write — blocked by the CARR guard"
    return None


def direct_metered_dispatch(cmd):
    """Refuse paid dispatches that bypass their reviewed budget wrapper.

    The wrapper itself is not an escape flag.  The guard sees only the command
    issued by the session; reviewed scripts perform their own in-process
    admission before reaching the vendor.  Inert PR bodies and documentation
    are stripped so describing a command is never mistaken for running it.
    """
    executable = strip_inert_text(cmd)
    patterns = (
        (re.compile(r"\b(?:npx\s+)?wrangler\s+(?:deploy|versions\s+(?:upload|deploy))\b", re.I),
         "direct Cloudflare release bypasses bin/deploy-worker.sh"),
        (re.compile(r"\bneonctl\b[^\n;&|]*\bbranches\s+create\b", re.I),
         "direct Neon branch create bypasses neon-disposable-branch admission"),
        (re.compile(r"\bgh\s+(?:workflow\s+run|run\s+rerun)\b", re.I),
         "direct GitHub Actions dispatch bypasses the remote-CI budget gate"),
    )
    for pattern, reason in patterns:
        if pattern.search(executable):
            return reason + " — blocked by the CARR metering gate"
    return None


# bypass audit C53 / AGENTS.md:225, REDESIGNED (2026-09-24, Opus review). The
# original version was a single regex over the whole `git add` argument list,
# which denied `git add -A hooks/x.py` (a NAMED path alongside -A) exactly as
# hard as a bare `git add -A` — a real false positive the replay against
# 12,145 real commands caught. This version tokenizes the argument list and
# denies ONLY the bare broad-add forms: `-A`, `--all`, a standalone `.`, the
# `:/` pathspec-magic form (matches from the worktree root, same reach as
# `-A`), or a combined short-flag cluster that includes `A` (`-Av`, `-fA`,
# …), with NO other pathspec token present. `git add -A <paths>` is
# explicitly allowed; git add's own semantics make that combination
# redundant, but redundant is not broad.
#
# Captures the segment between `git` and `add` (group 1, may hold `-C <dir>`)
# separately from the segment after `add` (group 2, the add arguments) — a
# second Opus re-review (2026-09-24) required resolving `git -C <dir> add`'s
# own directory, not just the session cwd.
_GIT_ADD = re.compile(r"\bgit\s+([^|;&\n]*?)\badd\b([^|;&\n]*)")

# A combined short-flag cluster: a single dash followed by one or more
# single-letter flags with no `=` and no second leading dash — `-Av`, `-fA`,
# `-vfA` are all this shape; `--all` and `-A` alone are handled as exact
# tokens above. Bare `-` and long options (`--foo`) never match.
_SHORT_FLAG_CLUSTER = re.compile(r"^-[A-Za-z]{2,}$")


def _bare_broad_add(argtext):
    """True when argtext's only pathspec-shaped tokens are -A/--all/bare '.'/
    the ':/' pathspec-magic form, or a combined short-flag cluster containing
    'A', and nothing else names a path. Flags other than -A/--all (or a
    cluster containing A) are ignored (git add -v -A is still bare); any
    other non-flag token is a real pathspec and clears the call."""
    try:
        tokens = shlex.split(argtext)
    except ValueError:
        return False
    broad_seen = False
    for tok in tokens:
        if tok in ("-A", "--all", ".", ":/"):
            broad_seen = True
            continue
        if _SHORT_FLAG_CLUSTER.match(tok):
            if "A" in tok[1:]:
                broad_seen = True
            continue
        if tok.startswith("-"):
            continue
        return False       # any other bare token is a real pathspec
    return broad_seen


def _in_carr_tree(cwd):
    """cwd is the canonical carr-system checkout or a path under it (which is
    where this repo's worktrees live, per bin/worktree.sh convention) —
    same shape as the functions.exec cwd scoping a few lines below in
    main()."""
    if not cwd:
        return False
    try:
        real_cwd = os.path.realpath(os.path.expanduser(cwd))
    except Exception:
        return False
    return real_cwd == REPO or real_cwd.startswith(REPO + os.sep)


def _strip_matched_quotes(tok):
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"'):
        return tok[1:-1]
    return tok


# A single leading `cd <dir> &&` or `cd <dir>;` — not a general shell
# interpreter, just the one shape the reported false positives/bypasses were
# built from (`cd /tmp/x && git add -A` from a carr cwd; `cd ~/carr-system &&
# git add -A` from /tmp). Anything more elaborate (a second cd, a subshell,
# `cd "$(...)"`) is out of scope, same as the other reported gaps below.
_LEADING_CD = re.compile(r"^\s*cd\s+(\"[^\"]*\"|'[^']*'|\S+)\s*(?:&&|;)\s*")


def _leading_cd_dir(cmd):
    """Return the directory named by a single leading `cd <dir> &&`/`;`, or
    None. Operates on the raw command: this is the command's own structure,
    not prose inside a quote, so it is read before strip_inert_text runs."""
    m = _LEADING_CD.match(cmd)
    if not m:
        return None
    return _strip_matched_quotes(m.group(1))


def _git_dash_c_dir(pre_add_argtext):
    """Return the directory named by a `-C <dir>` (or `-C<dir>`) flag found
    before `add` in the same git invocation, or None. `git -C <dir> add ...`
    runs against <dir>, not the process cwd — ignoring it both denies
    legitimate use from elsewhere (`git -C /tmp/x add -A` sent from a carr
    cwd) and misses a real bypass (`git -C ~/carr-system add -A` sent from
    /tmp)."""
    try:
        tokens = shlex.split(pre_add_argtext)
    except ValueError:
        return None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-C":
            return tokens[i + 1] if i + 1 < len(tokens) else None
        if tok.startswith("-C") and len(tok) > 2 and not tok.startswith("--"):
            return tok[2:]
        i += 1
    return None


def _resolve_dir(candidate, base):
    """Resolve `candidate` (from a leading cd or a -C flag) against `base`,
    the same way a shell would resolve a relative directory argument against
    the current directory."""
    expanded = os.path.expanduser(candidate)
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(base, expanded)


def broad_add_reason(cmd, cwd=None):
    """Return a reason string to deny a bare `git add -A`/`--all`/`.`/`:/`
    with no pathspec, scoped to the carr-system tree; None otherwise. Text
    inside quotes, heredocs, or a grep/echo argument is never matched for the
    add-arguments scan: it runs against strip_inert_text(cmd), the same
    inert-text stripper every other quote-safe rule in this file uses, not
    the raw command. The EFFECTIVE directory for the carr-tree scope check is
    resolved from a leading `cd <dir> &&`/`;` and/or a `-C <dir>` flag on the
    git invocation itself, not just the session's reported cwd — see
    _leading_cd_dir / _git_dash_c_dir."""
    session_cwd = cwd if cwd is not None else os.getcwd()
    leading_cd = _leading_cd_dir(cmd)
    base_cwd = _resolve_dir(leading_cd, session_cwd) if leading_cd else session_cwd
    scanned = strip_inert_text(cmd)
    for m in _GIT_ADD.finditer(scanned):
        pre_add, post_add = m.group(1), m.group(2)
        if not _bare_broad_add(post_add):
            continue
        c_dir = _git_dash_c_dir(pre_add)
        effective_cwd = _resolve_dir(c_dir, base_cwd) if c_dir else base_cwd
        if _in_carr_tree(effective_cwd):
            return ("broad add (-A/--all/./:/) — blocked by the CARR unattended guard. "
                    "Add explicit paths instead: `git add <path> [<path>...]`.")
    return None


def check(cmd, cwd=None):
    """Return a reason string to block, or None to allow."""
    if cmd.strip() in ALLOW_EXACT:
        return None

    reason = broad_add_reason(cmd, cwd)
    if reason:
        return reason

    reason = delegation_control_plane_write(cmd)
    if reason:
        return reason

    reason = direct_metered_dispatch(cmd)
    if reason:
        return reason

    for pat, label in RULES:
        if pat.search(cmd):
            # Destructive-fs rules are waived inside the sanctioned scratch zones.
            if label in ("recursive/forced delete", "secure delete") and in_safe_zone(cmd):
                continue
            # Rebasing your OWN branch in place is repair, not history loss —
            # and refusing it is what produced 35 orphan branches. Narrow by
            # design: lease spelling, named ref, never main. See the function.
            if label == "force push" and force_push_to_named_side_branch(cmd):
                continue
            # A SQL keyword sitting in PROSE is not a database operation (loop #240).
            # The patterns stay exactly as strict; they are simply consulted only when
            # the command could reach a database at all.
            if label in SQL_LABELS and not is_sql_context(cmd):
                continue
            # Prose ABOUT a destructive command is documentation. Consulted
            # only for the command-shaped labels, and only when the pattern is
            # absent from the executable remainder — so anything outside a
            # quoted prose flag or a heredoc body is still scanned exactly as
            # before. See PROSE_SAFE_LABELS for what is deliberately excluded.
            if label in PROSE_SAFE_LABELS and not pat.search(strip_inert_text(cmd)):
                continue
            return f"{label} — blocked by the CARR unattended guard"

    if is_send_context(cmd):
        for host in hosts_in(cmd):
            # host_allowlisted covers KNOWN_HOSTS plus the record-derived client
            # and lead domains. The Bash path gets NO equivalent of the WebFetch
            # open-read class and must not: curl chooses its own method and body,
            # so the length cap that makes an open GET safe buys nothing here.
            # This path stays allowlist-only, by design.
            if not host_allowlisted(host):
                return (f"network send to an unrecognised host ({host}) — blocked by the "
                        f"CARR unattended guard. Add it to KNOWN_HOSTS if it is legitimate, "
                        f"or — if it is a client's own domain — check that they carry a "
                        f"practice email and re-run ops/fetch-allowlist.py.")

    reason = render_write_target(cmd)
    if reason:
        return reason
    reason = corpus_render_write_target(cmd)
    if reason:
        return reason
    reason = vault_md_write_target(cmd)
    if reason:
        return reason
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                       # fail OPEN
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        ti = payload.get("tool_input") or payload.get("toolInput") or {}

        # [2026-08-06, loop #163 closed on Joe's "Fix both now"] WebFetch joins
        # the egress allowlist. Before this, `if tool != "Bash": sys.exit(0)`
        # meant WebFetch reached ANY host while the identical curl was blocked —
        # demonstrated live on 2026-08-03. Same KNOWN_HOSTS list, same tuning
        # path (a block names the host to add). WebSearch is deliberately NOT
        # gated: it reaches a search API, not an arbitrary host. Requires the
        # settings matcher to include WebFetch — changed the same sitting.
        if tool == "WebFetch":
            url = (ti.get("url", "") if isinstance(ti, dict) else "") or ""
            # urlsplit, not URL_RE, for the fetch path. The regex host class
            # excludes ':' and '@', so `https://user:pass@evil.com/` captures
            # "user" as the host — harmless while everything was deny-by-default,
            # actively wrong now that a host can pass on policy rather than on a
            # list. The Bash path below still uses URL_RE (it scans free text,
            # where a parser has no single URL to parse).
            try:
                _p = urlsplit(url if url.startswith(("http://", "https://")) else f"https://{url}")
                host = (_p.hostname or "").lower()
            except Exception:
                host = ""
            if host and not host_allowlisted(host):
                # Not on the list — fall through to the wider READ-ONLY class
                # rather than refusing outright. This is the 2026-08-09 split:
                # an allowlisted host keeps its unconditional pass, and anything
                # else must satisfy the open-read policy instead.
                reason = webfetch_open_read_reason(url)
                if reason:
                    log(f"DENY {reason} :: {url[:200]}")
                    print(reason, file=sys.stderr)
                    sys.exit(2)
                log(f"ALLOW(open-read) {host} :: {url[:200]}")
            sys.exit(0)

        # Codex may expose the outer functions.exec wrapper or its nested
        # exec_command. Apply the same shell policy to the literal command.
        if tool in {"functions.exec", "exec_command"}:
            # The native Bash guard pre-dates Codex and is intentionally global.
            # This new Codex alias is CARR-only so it cannot change Life AI or
            # another repository's workflow merely because they share Codex.
            cwd = ((ti.get("workdir") or payload.get("cwd") or "")
                   if isinstance(ti, dict) else (payload.get("cwd") or ""))
            try:
                real_cwd = os.path.realpath(os.path.expanduser(cwd))
            except Exception:
                real_cwd = ""
            if not (real_cwd == REPO or real_cwd.startswith(REPO + os.sep)
                    or "/CARR AI" in real_cwd):
                # A task rooted elsewhere can still target CARR by absolute
                # path.  Scope by the target too, otherwise a non-CARR cwd is
                # an accidental bypass for the very files this guard protects.
                raw = (ti if isinstance(ti, str) else
                       ((ti.get("cmd") or ti.get("code") or "")
                        if isinstance(ti, dict) else ""))
                if REPO not in raw and not raw_targets_carr(raw):
                    sys.exit(0)
            tool = "Bash"
        if tool != "Bash":
            sys.exit(0)
        # Codex's local-function tool passes freeform JavaScript as a string;
        # its embedded exec_command({cmd: ...}) must receive the same command
        # inspection as a native Bash call. A dict remains the Claude shape.
        cmd = (ti.get("command") or ti.get("cmd") or "") if isinstance(ti, dict) else ti
        if not isinstance(cmd, str):
            cmd = ""
        if not cmd:
            sys.exit(0)

        # Effective cwd for the broad-add repo scope: the tool_input's own
        # workdir (Codex), else the payload's cwd (Claude Code sends this for
        # every Bash call), else this process's own cwd as a last resort.
        effective_cwd = (
            (ti.get("workdir") if isinstance(ti, dict) else None)
            or payload.get("cwd") or os.getcwd())

        reason = check(cmd, effective_cwd)

        # BYPASS AUDIT C33/C34 (2026-09-24), REDESIGNED (2026-09-24, Opus
        # review). This hook used to re-run the four client verb gates
        # client-side against a parsed `./run.sh call <verb> '<json>'`
        # command (hooks/verb_gate_recheck.py, now deleted). A replay of
        # 12,145 real Bash commands found the shell-text side of that
        # approach fundamentally leaky (Jev: 0.93) — 62 legitimate commands
        # would have been falsely denied (`git add -A <paths>`, a grep for
        # the pattern text, fixture repos in /tmp…), and several trivial
        # bypasses (a shell variable holding the verb, `$(cat f)` JSON,
        # calling tools/call-verb.py or mcp-server/local-verb.mjs directly)
        # could never be closed from Bash-command-text at all — every one of
        # those doors recurses through the SAME server-side callTool(), so
        # the checks now live in the verb handlers themselves
        # (mcp-server/src/verb-gate-checks.js, wired into add-loop's handler
        # in tools.js) and this hook no longer duplicates them. Jev agreed
        # (0.94) that hosted CI is what actually stops a local-hook bypass
        # from reaching main, which is the other reason a client-side
        # regex recheck was the wrong enforcement point.

        # THE SHELL HALF OF rule 76a53dfe. A record refused at the vault must not
        # simply be written somewhere the gate does not look, and a heredoc into
        # a scratchpad was exactly that path. One shared memory with
        # record-home-gate.py, so a record refused through either door is
        # recognised at the other (rule a8c55a47: two doors, one module).
        session = payload.get("session_id") or payload.get("sessionId") or ""
        body = heredoc_body(cmd)
        if reason and "vault markdown" in reason:
            remember_refusal(body or cmd, session)
        elif not reason and body:
            hidden, share = was_refused(body, session)
            if hidden:
                log(f"DENY re-routed refused content ({share:.2f})")
                print(REFUSAL.format(pct=round(share * 100)), file=sys.stderr)
                sys.exit(2)

        if not reason:
            # THE GATE DOOR (loop #231, 2026-08-10). Runs only when nothing above
            # denies, so a destructive shape (`rm -rf hooks/`) still DENIES on the
            # stronger rule rather than being softened to an announcement here.
            #
            # Until today gate-edit-gate.py guarded Write/Edit on these files and
            # its docstring claimed this file guarded the shell path. It did not —
            # proven by firing the hook, not by reading it: append, sed -i, `>`
            # onto settings.json and tee all returned ALLOW while the render
            # control in the same run correctly DENIED. Shared matcher, one list,
            # two doors (rule a8c55a47): hooks/gate_paths.py.
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from gate_paths import announcement, enforcement_write
                hit = enforcement_write(cmd)
                if hit:
                    msg = announcement(hit, "a shell command")
                    log(f"ANNOUNCE gate-write {hit} :: {cmd[:300]}")
                    print(json.dumps({
                        "systemMessage": msg,
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "permissionDecisionReason": msg,
                        },
                    }))
                    sys.exit(0)
            except SystemExit:
                raise
            except Exception as exc:
                log(f"ALLOW(gate-door-error) {exc}")

        if reason:
            log(f"DENY {reason} :: {cmd[:300]}")
            # EXIT 2, NOT JSON, AND THE CHOICE MATTERS. The structured contract
            # (exit 0 + hookSpecificOutput.permissionDecision) is richer, but it
            # requires exit 0 — so on any build that does not parse the JSON, exit
            # 0 reads as ALLOW and the gate fails open silently. Exit 2 blocks on
            # every build and hands stderr back to the session as the reason. For
            # a guard, degrading toward "blocked" beats degrading toward "allowed".
            print(reason, file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    except Exception as exc:                       # fail OPEN
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
