# SocialRum Lab

A local creator-content research prototype. Start with a synthetic example, a CSV you provide, or an optional public Instagram snapshot.

## Quick start

- Send `demo` to explore fictional sample data.
- Send `upload` to analyze a CSV with `posted_at`, `creator`, and `content` columns.
- To search for a hashtag or phrase, send a command such as `find #finance`, `extract #finance`, or `extract personal finance`, or click one of the **Find**, **Extract**, or **Search a topic** buttons below the welcome message. Sending a bare hashtag or phrase does not start a search.
  - `find #finance` or `find personal finance` builds a Google search link for you to open. The app does not run this search or read its results.
  - `extract #finance` or `extract personal finance` runs a bounded search over visible Google results and attempts to read up to five public Instagram post/reel pages in the headed browser. This requires the `cdp` CLI and a reachable headed browser on the machine running the app. Click the candidate links in the response to inspect their sources.
- The separate **Collect reels** action or `collect #finance` / `collect personal finance` command asks for a readable target (1–50, default 25). Inline examples: `collect #finance --target 40` or `collect personal finance --target-readable 10`. Chainlit collection is reel-only. It has hard ceilings of 100 unique candidate attempts and 10 visible Google result pages, plus a soft 600-second deadline checked between operations; an in-flight CDP/browser operation can continue past that deadline. The app does not expose controls for these limits. It may return fewer records and reports attempts, readable count, and stop reason. The target counts only nonempty visible text with verified source association; ambiguous/unverified associations do not count.
- The `extract` result is best effort: its summary gives the Google query/time, matching readable post pages / readable posts (each post counts once), readable pages / candidates, and observed dates / readable posts, followed by candidate source links and statuses. Google’s index can be stale or incomplete, and pages may be unavailable. Search snippets are unverified context only and are **not used for keyword matching**. Matching uses visible post/caption text collected from the page. This is not live trend data, an exhaustive search, or a popularity ranking.
- `collect` discovers candidates through Google-indexed results, not Instagram live search. Google may omit, rank or retain pages unevenly. An optional `--recent-days` is available only in the terminal CLI and filters Google's index; it does not establish publication dates or recency. Snippets remain unverified context and do not count as collected text or matches. Partial runs preserve results already collected and report the stop reason.
- Use only public pages you are authorized to view and respect platform terms. This prototype uses no Meta API and does not require a Business/Creator account or Instagram login. The headed browser may already have its own account session; the app does not verify your authorization or whether that browser is logged out. It does not request credentials or log in; if a page requires login, stop and do not enter credentials in the app or chat.
- Only one public search (`extract` or `collect`) can run at a time across all chats handled by this app process. A request submitted while one is running is rejected, not queued; wait and send it again after the current search finishes.
- Saving is optional: after reviewing `extract finance`, send `capture finance` in the same chat. Later repeat `extract finance` and `capture finance`, then send `compare finance`. Send `forget finance` to delete local samples. Captures retain URLs, dates/status, and aggregate hashtag counts when available, not captions/snippets or per-post hashtag associations; comparisons describe only those samples.
- Send `instagram <keyword>` to enter a keyword and provide up to five public Instagram URLs. Each URL must match one already-open page in a headed browser tab by host and path; query strings and fragments are ignored. Tag pages use visible article text; individual `/p/` and `/reel/` pages may use a visible caption span when available. Instagram's same-slug `/explore/tags/<slug>/` to `/popular/<slug>/` redirect is accepted. Send `instagram urls` to provide URLs first and choose the keyword next.
- Send `draft 1` to make a draft from the top-ranked post. Use `edit`, `review`, and `save` to refine it.

## More commands

`profile` · `list` · `open <id>` · `export` · `captions` · `transcribe`

Drafts, processing outputs, and explicitly captured search snapshots are stored locally. In the manual Instagram URL flow, the app reads only visible text from matching pages you provide that are already open; `extract` opens the Google search and candidate Instagram pages after your command. Availability depends on the page. The app does not log in or publish. Results describe supplied or observed samples, not live trends. Rankings describe the supplied dataset and do not predict future performance.

For `transcribe`, the app uses an already-installed local Whisper CLI and model. Results are rough drafts that need review; the app does not download models or send media to a cloud service.
