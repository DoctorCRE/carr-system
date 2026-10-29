#!/usr/bin/env python3
"""
build-open-items-dashboard.py — regenerate the CARR one-page open-items dashboard.

Reads v_export_loops (status='open' rows only, joined-to-block view over every
loop kind: open_loop / team_loop / idea / action_required) via the read-only
exporter credential. There is currently no canonical document destination for
the static dashboard, so normal mode refuses before opening either the database
or Drive. The legacy Drive render is recovery-only.

Usage:
  cd ~/carr-system && ./.venv/bin/python generators/build-open-items-dashboard.py

Credential:
  ~/.config/carr/db.env carries CARR_DB_EXPORTER_URL. This script parses that
  file through lib.credential_file using the dashboard's literal quote rules.
  It does not shell out or import exporter machinery.

Recovery-only write:
  /Users/booko/My Drive/CARR AI/00_Context/open-items.html
"""

import html
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.credential_file import read_env_file  # noqa: E402

ENV_FILE = Path.home() / ".config" / "carr" / "db.env"
RECOVERY_DEFAULT_VAULT = Path("/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
REGEN_CMD = "cd ~/carr-system && ./.venv/bin/python generators/build-open-items-dashboard.py"

BELL = "\U0001F514"       # 🔔
CALENDAR = "\U0001F5D3"   # 🗓
QUESTION = "❓"       # ❓

LOOP_COLUMNS = [
    "rel_path", "kind", "number", "owner", "title", "body", "since_text",
    "unblocks", "source_note", "marker_literal", "status", "domain",
    "domain_label", "domain_sort", "block_seq", "render_seq",
]

GROUP_HOT = "hot"
GROUP_ACTION = "action"
GROUP_TEAM = "team"
GROUP_BACKLOG = "backlog"
GROUP_IDEA = "idea"

GROUP_LABELS: dict[str, str] = {
    GROUP_HOT: "Hot loops",
    GROUP_BACKLOG: "Backlog loops",
    GROUP_TEAM: "Team board",
    GROUP_IDEA: "Ideas",
    GROUP_ACTION: "Action required",
}

# "Vital few first": the order sections render on the page.
SECTION_ORDER: list[str] = [GROUP_HOT, GROUP_ACTION, GROUP_TEAM, GROUP_BACKLOG, GROUP_IDEA]
# The order the header's quick counts are listed in, per spec.
HEADER_COUNT_ORDER: list[str] = [GROUP_HOT, GROUP_BACKLOG, GROUP_TEAM, GROUP_IDEA, GROUP_ACTION]

EMPTY_STATE_TEXT = "Nothing here right now — that's a clean lane."

KIND_FALLBACK_LABEL: dict[str, str] = {
    "open_loop": "Open loop",
    "team_loop": "Team item",
    "idea": "Idea",
    "action_required": "Action item",
}

TITLE_MAX = 160

_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^\n]+?)\*\*")
_LINK_STRIP_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BOLD_STRIP_RE = re.compile(r"\*\*(.+?)\*\*")
_SENTENCE_RE = re.compile(r"\.(?=\s|$)")
_MARKER_DATE_RE = re.compile(r"^\U0001F5D3(\d{4}-\d{2}-\d{2})$")


# ── credential ────────────────────────────────────────────────────────────

def parse_env_file(path: Path) -> dict[str, str]:
    """Read the dashboard's literal KEY=value assignment format."""
    return read_env_file(path, syntax="paired") if path.exists() else {}


def get_dsn() -> str:
    file_env = parse_env_file(ENV_FILE)
    dsn = os.environ.get("CARR_DB_EXPORTER_URL") or file_env.get("CARR_DB_EXPORTER_URL")
    if not dsn:
        sys.exit(f"no CARR_DB_EXPORTER_URL found (checked environment and {ENV_FILE})")
    return dsn


def fetch_open_rows(dsn: str) -> list[dict[str, Any]]:
    # Normal mode refuses before this import, so even an interpreter without
    # psycopg cannot turn a missing dashboard destination into a Drive fallback.
    import psycopg
    from psycopg.rows import dict_row

    cols_sql = ", ".join(LOOP_COLUMNS)
    query = (
        f"select {cols_sql} from v_export_loops "
        "where status = %s "
        "order by domain_sort nulls last, kind, block_seq, render_seq"
    )
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, ("open",))
            rows = cur.fetchall()
    return list(rows)


# ── text transforms ──────────────────────────────────────────────────────

def render_body_html(body: str | None) -> str:
    """Escape HTML first, then convert **bold** and [text](url) links only."""
    if not body:
        return ""
    escaped = html.escape(body, quote=True)
    escaped = _LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>',
        escaped,
    )
    escaped = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    paragraphs = re.split(r"\n\s*\n", escaped.strip())
    parts: list[str] = []
    for para in paragraphs:
        if not para.strip():
            continue
        parts.append("<p>" + para.replace("\n", "<br>") + "</p>")
    return "".join(parts)


def derive_title(title: str | None, body: str | None) -> str:
    """The record's own title, or a plain-words title lifted off the body."""
    if title and title.strip():
        return title.strip()
    if not body:
        return "(untitled)"
    plain = _LINK_STRIP_RE.sub(r"\1", body)
    plain = _BOLD_STRIP_RE.sub(r"\1", plain).strip()
    if not plain:
        return "(untitled)"

    para_idx: int | None = None
    for sep in ("\n\n", "\n"):
        idx = plain.find(sep)
        if idx != -1:
            para_idx = idx if para_idx is None else min(para_idx, idx)
    candidate = plain if para_idx is None else plain[:para_idx]
    truncated = para_idx is not None

    m = _SENTENCE_RE.search(candidate[:220])
    if m and m.start() > 20:
        candidate = candidate[: m.start() + 1]
        truncated = False

    if len(candidate) > TITLE_MAX:
        cut = candidate[:TITLE_MAX]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        candidate = cut
        truncated = True

    candidate = candidate.strip()
    if truncated:
        candidate = candidate.rstrip(" .,;:-—") + "…"
    return candidate or "(untitled)"


def marker_date(marker: str | None) -> date | None:
    if not marker:
        return None
    m = _MARKER_DATE_RE.match(marker.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def is_hot_open_loop(marker: str | None, today: date) -> bool:
    if not marker:
        return False
    m = marker.strip()
    if m == BELL:
        return True
    d = marker_date(m)
    return d is not None and d <= today


def group_of(kind: str, marker: str | None, today: date) -> str:
    if kind == "action_required":
        return GROUP_ACTION
    if kind == "team_loop":
        return GROUP_TEAM
    if kind == "idea":
        return GROUP_IDEA
    if kind == "open_loop":
        return GROUP_HOT if is_hot_open_loop(marker, today) else GROUP_BACKLOG
    return GROUP_BACKLOG  # unexpected kind: still shown, never dropped


def marker_chip(marker: str | None, kind: str) -> tuple[str, str]:
    """(visible chip text, aria-label) — the marker glyph if present, else the kind."""
    if marker:
        m = marker.strip()
        if m == BELL:
            return BELL, "Reminder"
        d = marker_date(m)
        if d is not None:
            return f"{CALENDAR} {d.isoformat()}", f"Due {d.isoformat()}"
        if m.startswith(CALENDAR):
            rest = m[len(CALENDAR):]
            return f"{CALENDAR} {rest}", f"Marked {rest}"
        if m == QUESTION:
            return QUESTION, "Needs a decision"
        return m, m
    label = KIND_FALLBACK_LABEL.get(kind, kind.replace("_", " ").title())
    return label, label


def domain_key_label(domain: str | None, domain_label: str | None) -> tuple[str, str]:
    key = domain or "none"
    if domain_label:
        label = domain_label.split(" — ", 1)[0].strip()
    elif domain:
        label = domain.replace("_", " ").title()
    else:
        label = "Uncategorized"
    return key, label


# ── card building ────────────────────────────────────────────────────────

class Card:
    __slots__ = (
        "card_id", "group", "kind", "title", "chip_text", "chip_aria", "due_date",
        "owner", "domain_key", "domain_label", "since_text", "number",
        "body_html", "unblocks", "source_note", "rel_path", "search_text",
    )

    def __init__(
        self,
        card_id: str,
        group: str,
        kind: str,
        title: str,
        chip_text: str,
        chip_aria: str,
        due_date: date | None,
        owner: str,
        domain_key: str,
        domain_label: str,
        since_text: str,
        number: str,
        body_html: str,
        unblocks: str,
        source_note: str,
        rel_path: str,
        search_text: str,
    ) -> None:
        self.card_id = card_id
        self.group = group
        self.kind = kind
        self.title = title
        self.chip_text = chip_text
        self.chip_aria = chip_aria
        self.due_date = due_date
        self.owner = owner
        self.domain_key = domain_key
        self.domain_label = domain_label
        self.since_text = since_text
        self.number = number
        self.body_html = body_html
        self.unblocks = unblocks
        self.source_note = source_note
        self.rel_path = rel_path
        self.search_text = search_text


def build_cards(rows: list[dict[str, Any]], today: date) -> list[Card]:
    cards: list[Card] = []
    for i, row in enumerate(rows):
        kind = str(row.get("kind") or "")
        marker = row.get("marker_literal")
        marker_s = str(marker) if marker else None
        group = group_of(kind, marker_s, today)
        title_raw = row.get("title")
        body_raw = row.get("body")
        title = derive_title(
            str(title_raw) if title_raw else None,
            str(body_raw) if body_raw else None,
        )
        chip_text, chip_aria = marker_chip(marker_s, kind)
        due_date = marker_date(marker_s)
        owner = str(row.get("owner") or "").strip()
        domain_key, domain_label = domain_key_label(
            (str(row.get("domain")) if row.get("domain") else None),
            (str(row.get("domain_label")) if row.get("domain_label") else None),
        )
        since_text = str(row.get("since_text") or "").strip()
        number = str(row.get("number") or "").strip()
        unblocks = str(row.get("unblocks") or "").strip()
        source_note = str(row.get("source_note") or "").strip()
        rel_path = str(row.get("rel_path") or "").strip()
        body_html = render_body_html(str(body_raw) if body_raw else None)
        search_parts = [title, str(body_raw or ""), owner, number]
        search_text = " ".join(p.lower() for p in search_parts if p)
        card_id = f"card-{i}-{kind}-{number or i}"
        card_id = re.sub(r"[^a-zA-Z0-9\-]", "-", card_id)
        cards.append(Card(
            card_id=card_id, group=group, kind=kind, title=title,
            chip_text=chip_text, chip_aria=chip_aria, due_date=due_date, owner=owner,
            domain_key=domain_key, domain_label=domain_label,
            since_text=since_text, number=number, body_html=body_html,
            unblocks=unblocks, source_note=source_note, rel_path=rel_path,
            search_text=search_text,
        ))

    # Order within each group: HOT surfaces the nearest due date first, then
    # bell reminders; everything else stays in the view's own (domain, block,
    # render) order, which is already the order the rows arrived in.
    def hot_sort_key(c: Card) -> tuple[int, str]:
        if c.due_date is not None:
            return (0, c.due_date.isoformat())
        return (1, c.title.lower())

    hot = [c for c in cards if c.group == GROUP_HOT]
    hot.sort(key=hot_sort_key)
    rest = [c for c in cards if c.group != GROUP_HOT]
    by_group: dict[str, list[Card]] = {g: [] for g in SECTION_ORDER}
    by_group[GROUP_HOT] = hot
    for c in rest:
        by_group[c.group].append(c)
    ordered: list[Card] = []
    for g in SECTION_ORDER:
        ordered.extend(by_group[g])
    return ordered


# ── HTML rendering ───────────────────────────────────────────────────────

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def render_card(c: Card) -> str:
    meta_bits = [b for b in (c.owner, c.domain_label, c.since_text) if b]
    meta_line = esc(" · ".join(meta_bits)) if meta_bits else ""
    number_html = f'<span class="meta-number">#{esc(c.number)}</span>' if c.number else ""
    extra_lines = []
    if c.unblocks:
        extra_lines.append(
            f'<p class="card-extra"><span class="card-extra-label">Unblocks:</span> {esc(c.unblocks)}</p>'
        )
    if c.source_note:
        extra_lines.append(
            f'<p class="card-extra"><span class="card-extra-label">Source:</span> {esc(c.source_note)}</p>'
        )
    if c.rel_path:
        extra_lines.append(f'<p class="card-provenance">from {esc(c.rel_path)}</p>')
    extra_html = "".join(extra_lines)
    body_html = c.body_html or "<p><em>(no further detail recorded)</em></p>"

    return f"""
      <article class="card" data-group="{esc(c.group)}" data-domain="{esc(c.domain_key)}"
               data-search="{esc(c.search_text)}">
        <div class="card-head">
          <h3 class="card-title">{esc(c.title)}</h3>
          <span class="chip chip-marker" role="img" aria-label="{esc(c.chip_aria)}">{esc(c.chip_text)}</span>
        </div>
        <p class="card-meta">{meta_line}{" &middot; " if meta_line and number_html else ""}{number_html}</p>
        <details class="card-body">
          <summary>More</summary>
          <div class="card-body-inner">
            {body_html}
            {extra_html}
          </div>
        </details>
      </article>"""


def render_section(group: str, cards: list[Card]) -> str:
    label = GROUP_LABELS[group]
    open_attr = " open" if group == GROUP_HOT else ""
    n = len(cards)
    cards_html = "".join(render_card(c) for c in cards)
    empty_hidden = "" if n == 0 else " hidden"
    return f"""
    <section class="group" data-group="{esc(group)}">
      <details{open_attr}>
        <summary>
          <span class="summary-title">{esc(label)}</span>
          <span class="summary-count" data-base="{n}">{n} open</span>
        </summary>
        <div class="cards" data-group-cards="{esc(group)}">{cards_html}
        </div>
        <p class="empty-state"{empty_hidden}>{esc(EMPTY_STATE_TEXT)}</p>
      </details>
    </section>"""


CSS = """
:root {
  --navy: #002F6C;
  --navy-deep: #00224D;
  --orange: #F57F29;
  --bg: #F4F6F9;
  --surface: #FFFFFF;
  --border: #DDE3ED;
  --text: #00224D;
  --dim: #5B6B84;
  --font-display: Oswald, 'Arial Narrow', sans-serif;
  --font-body: Montserrat, 'Helvetica Neue', sans-serif;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0; background: var(--bg); color: var(--text);
  font-family: var(--font-body); line-height: 1.45;
}
body { padding-bottom: 3rem; }
h1, h2, h3, .summary-title, .card-title { font-family: var(--font-display); font-weight: 600; }
a { color: var(--navy); }
a:hover { color: var(--orange); }

/* Focus ring: orange against the navy header/controls (where it lives at
   4.9:1 contrast); navy-deep elsewhere, so every focus indicator clears
   WCAG AA non-text contrast (3:1) against the surface it actually sits on. */
:focus-visible { outline: 3px solid var(--navy-deep); outline-offset: 2px; border-radius: 4px; }
header :focus-visible, .controls :focus-visible.chip { outline-color: var(--orange); }
.on-navy :focus-visible { outline-color: var(--orange); }

header.page-header {
  background: var(--navy);
  color: #fff;
  padding: 1.5rem 1.25rem;
}
header.page-header h1 {
  margin: 0 0 0.35rem 0;
  font-size: clamp(1.5rem, 3vw, 2.1rem);
  letter-spacing: 0.02em;
}
.generated-at { color: #C9D6EA; font-size: 0.9rem; margin: 0 0 1rem 0; }
.counts-row {
  display: flex; flex-wrap: wrap; gap: 0.6rem 1rem; list-style: none;
  margin: 0; padding: 0; font-size: 0.92rem;
}
.counts-row li { white-space: nowrap; }
.counts-row .count-total { font-weight: 700; color: #fff; }
.counts-row .count-num { color: var(--orange); font-weight: 700; }
.counts-row .count-label { color: #DCE6F5; }

.controls {
  background: var(--navy-deep);
  padding: 0.9rem 1.25rem;
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem 0.9rem;
}
.controls .search-wrap { flex: 1 1 240px; min-width: 200px; }
#search {
  width: 100%; min-height: 44px; border-radius: 6px; border: 1px solid transparent;
  padding: 0 0.9rem; font-size: 1rem; font-family: var(--font-body);
  background: #fff; color: var(--text);
}
.chip-group { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.chip-group-label {
  color: #C9D6EA; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em;
  align-self: center; margin-right: 0.15rem;
}
button.chip {
  min-height: 44px; padding: 0.4rem 0.9rem; border-radius: 999px;
  border: 1.5px solid #3A548E; background: transparent; color: #fff;
  font-family: var(--font-body); font-size: 0.88rem; cursor: pointer;
}
button.chip[aria-pressed="true"] {
  background: var(--orange); border-color: var(--orange); color: var(--navy-deep); font-weight: 700;
}
button.chip:hover { border-color: var(--orange); }
#clear-filters {
  min-height: 44px; padding: 0.4rem 0.9rem; border-radius: 6px;
  border: 1.5px solid #3A548E; background: transparent; color: #fff;
  font-family: var(--font-body); font-size: 0.88rem; cursor: pointer;
}
#clear-filters:hover { border-color: var(--orange); }

main { max-width: 980px; margin: 0 auto; padding: 1.25rem; }

.group { margin-bottom: 1rem; }
.group details {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  overflow: hidden;
}
.group summary {
  list-style: none; cursor: pointer; padding: 0.9rem 1.1rem;
  display: flex; align-items: center; justify-content: space-between; gap: 0.75rem;
  min-height: 44px; background: var(--surface); font-size: 1.1rem;
}
.group summary::-webkit-details-marker { display: none; }
.group summary::before {
  content: "\\25B8"; color: var(--navy); margin-right: 0.6rem; display: inline-block;
}
.group details[open] summary::before { content: "\\25BE"; }
@media (prefers-reduced-motion: no-preference) {
  .group summary::before { transition: transform 0.15s ease; }
}
.summary-title { color: var(--navy-deep); }
.summary-count { color: var(--dim); font-size: 0.9rem; font-weight: 500; white-space: nowrap; }

.cards {
  padding: 0.25rem 1.1rem 0.9rem 1.1rem; display: grid; gap: 0.75rem;
  border-top: 1px solid var(--border);
}

.card {
  border: 1px solid var(--border); border-radius: 8px; padding: 0.8rem 0.95rem;
  background: var(--surface);
}
.card.is-filtered-out { display: none; }
.card-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 0.75rem;
}
.card-title { margin: 0; font-size: 1.02rem; color: var(--text); font-weight: 600; flex: 1 1 auto; }
.chip-marker {
  flex: 0 0 auto; background: var(--bg); border: 1px solid var(--border); border-radius: 999px;
  padding: 0.15rem 0.55rem; font-size: 0.95rem; white-space: nowrap;
}
.card-meta { margin: 0.3rem 0 0.4rem 0; font-size: 0.85rem; color: var(--dim); }
.meta-number { font-size: 0.85rem; color: var(--dim); font-weight: 400; }

.card-body summary {
  cursor: pointer; color: var(--navy); font-size: 0.85rem; font-weight: 600;
  min-height: 32px; display: inline-flex; align-items: center;
  list-style: none;
}
.card-body summary::-webkit-details-marker { display: none; }
.card-body summary::before { content: "+ "; }
.card-body[open] summary::before { content: "\\2212 "; }
.card-body-inner { margin-top: 0.35rem; font-size: 0.92rem; }
.card-body-inner p { margin: 0 0 0.6rem 0; }
.card-body-inner p:last-child { margin-bottom: 0; }
.card-extra { font-size: 0.86rem; color: var(--dim); }
.card-extra-label { font-weight: 700; color: var(--text); }
.card-provenance { font-size: 0.78rem; color: var(--dim); font-style: italic; }

.empty-state {
  margin: 0.5rem 1.1rem 0.9rem 1.1rem; padding: 0.9rem; text-align: center;
  color: var(--dim); background: var(--bg); border: 1px dashed var(--border); border-radius: 8px;
}

footer.page-footer {
  max-width: 980px; margin: 1.5rem auto 0 auto; padding: 0 1.25rem;
  color: var(--dim); font-size: 0.78rem;
}
footer.page-footer code {
  background: var(--surface); border: 1px solid var(--border); border-radius: 4px;
  padding: 0.1rem 0.35rem;
}
"""

JS = """
(function () {
  var search = document.getElementById('search');
  var kindChips = Array.prototype.slice.call(document.querySelectorAll('.chip-kind'));
  var domainChips = Array.prototype.slice.call(document.querySelectorAll('.chip-domain'));
  var clearBtn = document.getElementById('clear-filters');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var sections = Array.prototype.slice.call(document.querySelectorAll('.group'));

  var activeKinds = new Set();
  var activeDomains = new Set();

  function toggleChip(chip, set) {
    var val = chip.getAttribute('data-value');
    var pressed = chip.getAttribute('aria-pressed') === 'true';
    if (pressed) {
      chip.setAttribute('aria-pressed', 'false');
      set.delete(val);
    } else {
      chip.setAttribute('aria-pressed', 'true');
      set.add(val);
    }
    applyFilters();
  }

  kindChips.forEach(function (chip) {
    chip.addEventListener('click', function () { toggleChip(chip, activeKinds); });
  });
  domainChips.forEach(function (chip) {
    chip.addEventListener('click', function () { toggleChip(chip, activeDomains); });
  });

  function applyFilters() {
    var q = (search.value || '').trim().toLowerCase();
    cards.forEach(function (card) {
      var matchesSearch = !q || (card.getAttribute('data-search') || '').indexOf(q) !== -1;
      var group = card.getAttribute('data-group');
      var domain = card.getAttribute('data-domain');
      var matchesKind = activeKinds.size === 0 || activeKinds.has(group);
      var matchesDomain = activeDomains.size === 0 || activeDomains.has(domain);
      var show = matchesSearch && matchesKind && matchesDomain;
      card.classList.toggle('is-filtered-out', !show);
    });
    sections.forEach(function (section) {
      var group = section.getAttribute('data-group');
      var groupCards = Array.prototype.slice.call(
        section.querySelectorAll('.card')
      );
      var visible = groupCards.filter(function (c) {
        return !c.classList.contains('is-filtered-out');
      }).length;
      var countEl = section.querySelector('.summary-count');
      var base = parseInt(countEl.getAttribute('data-base'), 10) || 0;
      var filtersActive = q || activeKinds.size > 0 || activeDomains.size > 0;
      countEl.textContent = filtersActive ? (visible + ' of ' + base + ' shown') : (base + ' open');
      var emptyEl = section.querySelector('.empty-state');
      if (emptyEl) {
        emptyEl.hidden = visible > 0;
      }
    });
  }

  search.addEventListener('input', applyFilters);

  clearBtn.addEventListener('click', function () {
    search.value = '';
    activeKinds.clear();
    activeDomains.clear();
    kindChips.concat(domainChips).forEach(function (chip) {
      chip.setAttribute('aria-pressed', 'false');
    });
    applyFilters();
  });

  applyFilters();
})();
"""


def build_html(cards: list[Card], generated_at: str) -> str:
    total = len(cards)
    group_counts: Counter[str] = Counter(c.group for c in cards)
    domain_counts: Counter[str] = Counter(c.domain_key for c in cards)
    domain_labels: dict[str, str] = {}
    for c in cards:
        domain_labels.setdefault(c.domain_key, c.domain_label)

    counts_items = "".join(
        f'<li><span class="count-num">{group_counts.get(g, 0)}</span> '
        f'<span class="count-label">{esc(GROUP_LABELS[g])}</span></li>'
        for g in HEADER_COUNT_ORDER
    )
    counts_html = (
        f'<li class="count-total">{total} open item{"s" if total != 1 else ""}</li>'
        + counts_items
    )

    kind_chips_html = "".join(
        f'<button type="button" class="chip chip-kind" data-value="{esc(g)}" '
        f'aria-pressed="false">{esc(GROUP_LABELS[g])} ({group_counts.get(g, 0)})</button>'
        for g in HEADER_COUNT_ORDER
    )

    domain_order = sorted(
        domain_labels.keys(),
        key=lambda k: (k == "none", domain_labels[k].lower()),
    )
    domain_chips_html = "".join(
        f'<button type="button" class="chip chip-domain" data-value="{esc(k)}" '
        f'aria-pressed="false">{esc(domain_labels[k])} ({domain_counts.get(k, 0)})</button>'
        for k in domain_order
    )

    by_group: dict[str, list[Card]] = {g: [] for g in SECTION_ORDER}
    for c in cards:
        by_group[c.group].append(c)
    sections_html = "".join(render_section(g, by_group[g]) for g in SECTION_ORDER)

    return f"""<!-- GENERATED by generators/build-open-items-dashboard.py — do not hand-edit; regenerate with: {REGEN_CMD} -->
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open Items — everything, one page</title>
<style>{CSS}</style>
</head>
<body>
<header class="page-header on-navy">
  <h1>Open Items — everything, one page</h1>
  <p class="generated-at">Generated {esc(generated_at)}</p>
  <ul class="counts-row">{counts_html}</ul>
</header>
<div class="controls on-navy">
  <div class="search-wrap">
    <label for="search" class="visually-hidden" style="position:absolute;left:-9999px;">Search open items</label>
    <input type="search" id="search" placeholder="Search title, body, owner, or number…" autocomplete="off">
  </div>
  <div class="chip-group" role="group" aria-label="Filter by kind">
    <span class="chip-group-label">Kind</span>
    {kind_chips_html}
  </div>
  <div class="chip-group" role="group" aria-label="Filter by domain">
    <span class="chip-group-label">Domain</span>
    {domain_chips_html}
  </div>
  <button type="button" id="clear-filters">Clear filters</button>
</div>
<main>
{sections_html}
</main>
<footer class="page-footer">
  <p>Regenerate: <code>{esc(REGEN_CMD)}</code></p>
</footer>
<script>{JS}</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the open-items dashboard")
    parser.add_argument("--recovery", action="store_true",
                        help="write the legacy Drive projection (explicitly noncanonical)")
    parser.add_argument("--reason", help="why the recovery projection is necessary")
    parser.add_argument("--vault", type=Path, help="recovery Drive root; requires --recovery")
    args = parser.parse_args()
    if args.vault and not args.recovery:
        parser.error("--vault is recovery-only; pass --recovery explicitly")
    if args.recovery and not (args.reason and args.reason.strip()):
        parser.error("--recovery requires a nonblank --reason")
    return args


def main() -> None:
    args = parse_args()
    if not args.recovery:
        sys.exit(
            "MISSING_CANONICAL_SEAM: open-items dashboard document destination; "
            "normal mode refuses the retired Drive projection"
        )

    # This legacy location is eligible only after the explicit recovery door
    # above; an empty ambient value must not turn into a current-directory root.
    ambient_recovery_vault = os.environ.get("CARR_VAULT")
    vault = args.vault or Path(
        ambient_recovery_vault if ambient_recovery_vault else RECOVERY_DEFAULT_VAULT
    )
    out_path = vault / "00_Context" / "open-items.html"
    print(f"RECOVERY NONCANONICAL: Drive dashboard projection; reason={args.reason}", file=sys.stderr)
    dsn = get_dsn()
    rows = fetch_open_rows(dsn)
    today = date.today()
    cards = build_cards(rows, today)

    group_counts: Counter[str] = Counter(c.group for c in cards)
    total = len(cards)
    summed = sum(group_counts.get(g, 0) for g in SECTION_ORDER)
    if summed != total:
        sys.exit(f"EXIT 1: per-section counts ({summed}) do not sum to total open rows ({total})")

    now = datetime.now().astimezone()
    generated_at = now.strftime("%B %-d, %Y · %-I:%M %p %Z").strip()

    out_html = build_html(cards, generated_at)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".html.tmp")
    tmp_path.write_text(out_html, encoding="utf-8")
    os.replace(tmp_path, out_path)

    size = out_path.stat().st_size
    breakdown = " | ".join(f"{GROUP_LABELS[g]}: {group_counts.get(g, 0)}" for g in HEADER_COUNT_ORDER)
    print(
        f"Wrote {out_path} ({size:,} bytes) — {total} open total | {breakdown}"
    )


if __name__ == "__main__":
    main()
