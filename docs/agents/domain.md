# Domain Docs

How the engineering skills should consume this repo's domain documentation.

Layout: **single-context**. The glossary and the decision record live in the CARR
doctrine store and decision log, not in repo files. Do not create `GLOSSARY.md` or
`docs/adr/`: the write law sends domain content through record verbs.

## Before exploring, read these

- **Glossary**: `search-doctrine` for the term, then `read-doctrine` for the
  section. `doctrine-index` lists the documents. `./run.sh retrieve "<question>"`
  is the broad lookup.
- **Decisions (ADRs)**: the decision log, written by `log-decision` and changed
  by `update-decision`. Search it by what a decision does, not by a label.
- **Repo-local contracts**: `AGENTS.md` and the files it links.

If the store is unreachable, say so and stop. Do not work from memory.

## Use the doctrine's vocabulary

When your output names a domain concept (an issue title, a refactor proposal, a
test name), use the term as the doctrine defines it. Example: the app persona is
**Dr. CRE**; "Doc" is only the spoken nickname.

If a concept has no doctrine section, either the language is invented
(reconsider) or there is a gap. Record the gap with `/domain-modeling`, which
writes through `write-doctrine-section` and `log-decision`.

## Flag decision conflicts

If your output contradicts a logged decision, say so explicitly:

> _Contradicts the decision that conduct rules live in hooks, but worth reopening because…_
