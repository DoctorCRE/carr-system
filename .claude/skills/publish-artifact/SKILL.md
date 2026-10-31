---
name: publish-artifact
description: Publish a page as a private claude.ai Artifact from a session that has no Artifact tool, such as a Claude, Codex or Grok thread inside T3 Code. Use when Joe or Dell asks for an artifact, a shareable page or a dashboard link and your tool list has no Artifact tool.
---

# Publish an Artifact without the Artifact tool

T3 Code starts Claude through Anthropic's Agent SDK, and Claude Code hides the Artifact tool on that entrypoint. The `claude` CLI started directly keeps it. So hand the publish to one headless CLI call:

1. Write the page as a self-contained `.html` file in a scratch folder outside the repo. Follow the artifact design rules: a `<title>` of two to four words, light and dark color tokens, phone-width layout, scripts only from the approved CDNs.
2. Publish it:

   ```sh
   claude -p "Publish <absolute path>.html with the Artifact tool, icon: <one word>. Reply with only the URL or the exact error." \
     --model claude-sonnet-5-5 --allowedTools "Artifact" < /dev/null
   ```

   To update the same artifact later, add "Update the existing artifact <url>; read it first" to the prompt.
3. Reply with the URL the call printed. The page is private until its owner shares it.

For a page that only needs to show inside the thread, use T3's visual replies instead: ask for the answer "as a page", and T3 renders it in the thread on desktop, web and phone.
