# Queued job house rules

Never run env, printenv or ps eww or print environment variables.
Never open .env, credentials, tokens, keys, keychains or DB dumps.
No production writes except through record-layer verbs.
Do not edit the live ~/carr-system or ~/doctorcre-app. Work in an isolated checkout.
Never rebase or force-push.
Never poll GitHub. Push, run gh pr merge --auto once, report, stop.
Never post PR comments or reviews containing VERDICT, Reviewed-SHA or Reviewer:. Never approve your own work.
Never put "[skip ci]" in PR titles or commits.
The PR body must contain a standalone "Card: WRn.Sm.Jk" line, using the exact card ID supplied in the brief, without quotes or trailing text.
Commits use jbookout@users.noreply.github.com and include "Co-Authored-By: Codex <noreply@openai.com>".
Report the PR link, head SHA, tests and net lines.
