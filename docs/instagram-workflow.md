# Instagram keyword snapshot

This optional workflow provides best-effort analysis of text from Instagram pages you provide, post/reel pages surfaced by a user-initiated Google search, or excerpts you paste yourself. It is not a complete post collector, a live trend feed, or a popularity ranking. The bundled demo and CSV analysis remain the reliable offline paths.

Instagram's terms restrict automated collection and access without express permission. Review the [Instagram Terms of Use](https://help.instagram.com/581066165581870) and only use this feature when you have permission to access the content in this way. The user-triggered `extract` command opens a Google search and candidate Instagram post/reel pages in the local headed browser, then reads visible text when available. The supplied-URL flow reads a matching page that you already opened. The app does not request or store Instagram credentials, sign in, bypass a login wall, click or scroll pages, inspect hidden browser state, or publish anything. If a page asks you to log in, stop; do not provide credentials to the app. Use the manual route only for public text you can legitimately view and share.

## How to run it

### Standalone script for a single post

From the project directory, open one public post or reel in the headed browser
that the local `cdp` CLI can reach. Then run:

```bash
python3 scripts/extract_instagram_post.py 'https://www.instagram.com/p/POST_ID/'
```

Replace the URL with the exact page you opened. Before running it, make sure
your use complies with Meta's policies and that you have permission for
automated collection; public visibility or research intent alone does not
establish permission. The script cannot check this permission or determine
whether the browser is signed in or whether a page is publicly accessible. It
prints the visible caption and any uniquely observed creator/date as JSON to
the terminal; it does not save a file. It neither opens pages nor signs in, and
it stops at access walls.
The observed caption selector is fragile, so page changes or ambiguous text can
cause a clear failure. This script reads one selected post; it does not search
Instagram by keyword or establish trends.

1. Start SocialRum Lab on the machine where its `cdp` command and headed browser are available. The app runs `cdp` locally as a subprocess, so `cdp` must be on the `PATH` inherited by the app process.
2. In that machine's headed browser, open the public Instagram page you intend to inspect. The collector matches a supplied URL to an already-open page by Instagram host and path; query strings and fragments are ignored. It also accepts Instagram's `/explore/tags/<slug>` URL when the open tab has redirected to `/popular/<slug>`. It does not navigate to a URL for you.
3. In the Chainlit chat, send `instagram <keyword>` (for example, `instagram personal finance`). The app asks for one to five public Instagram URLs, one per line. If you prefer to enter the keyword afterward, send `instagram urls`; it asks for the URLs and then the keyword.
4. Submit the URLs. For hashtag/tag pages, the app reads visible text from up to 50 `article` elements per URL, with each text item limited to 4,000 characters. For a single post or reel permalink, it uses one narrowly observed caption selector and requires exactly one visible match. It reports matching text, provenance, and a possible angle, or returns an error/empty result if content is inaccessible or the selector is missing or ambiguous. It does not work around those conditions.

Each supplied URL must match exactly one open headed tab by Instagram host and path. Query strings and fragments are ignored for this comparison. The one supported path redirect is `/explore/tags/<slug>` matching `/popular/<slug>` on the same host. If Instagram shows optional cookie consent, handle that prompt manually in the headed browser on the machine running the app; the collector itself does not click, scroll, or accept prompts. Never enter login credentials in the app or chat.

### What has been observed

One test successfully collected a single historical public post at [instagram.com/p/CnwF6v8uA3U](https://www.instagram.com/p/CnwF6v8uA3U/), after optional cookie consent was declined manually in the headed browser. The app returned one observation, its source, and a suggested angle for the keyword `currency`. This confirms one page/selector path only; it does not establish reliable extraction across posts or accounts, current trend coverage, or popularity. The single-post selector depends on a hashed Instagram CSS class observed on that page and may break when Instagram changes its markup.

The direct public finance hashtag page tested separately still returned a login wall and no observations. Direct tag-page collection therefore remains unverified. A single historical post result is not evidence of current trends, and missing/blocked posts are not evidence that no matching posts exist.

### Search and collect public posts

For the legacy five-candidate flow, send `extract #finance` or `extract personal finance`. It searches visible Google results and attempts to read up to five Instagram post/reel candidates. It does not query Instagram's live search. The existing `find #finance` or `find personal finance` command remains the link-only option: it creates a Google search link for you to open and does not run the search or read results.

#### Separate readable-reel collection

The new `collect` workflow has a readable-record target, separate from the five-candidate `extract` flow. In Chainlit, click **Collect reels**, enter a phrase or hashtag, and choose a target from 1–50 (blank means 25). Or use a command directly:

```text
collect #education
collect personal finance --target 40
```

`--target N` and `--target-readable N` are accepted by the Chainlit command. Target means unique reels with nonempty visible caption text **and verified caption-to-requested-reel association**. A readable but unverified/ambiguous association is excluded from the target count. The run may return fewer records; the UI reports the target, collected count, attempts and stop reason. Only one public search runs at a time in the app process. A request submitted while one is running is rejected, not queued.

From a terminal, run the separate collector on the machine where the local headed browser and `cdp` CLI are available:

```bash
python3 scripts/search_instagram_posts.py '#education' --target-readable 25
```

The phrase/hashtag is positional. Reel-only discovery is the default. Use `--include-posts` to opt into a mixed post/reel sample; `--reels-only` remains accepted explicitly. Supported controls and defaults are:

| Flag | Default | Bounds / meaning |
|---|---:|---|
| `--target-readable N` | 25 | 1–50 unique, nonempty records with verified source association; may return fewer |
| `--max-attempts N` | 100 | 1–100 unique candidate read attempts; `--limit` remains a compatibility alias for this attempt bound, not the readable target |
| `--max-pages N` | 10 | 1–10 visible Google result pages |
| `--time-budget-seconds N` | 600 | 1–600 seconds; soft deadline checked between operations, not a hard wall-clock cap |
| `--reels-only` | on | Explicitly request reel-only candidate discovery; this is already the default. |
| `--include-posts` | off | Include Instagram post URLs alongside reels for a mixed sample. |
| `--recent-days D` | unset | 1–365 day filter requested from Google; not a verified Instagram publication-date filter |

The JSON output keeps candidate text, snippets, attempts, readable count, pages visited, elapsed time and `stop_reason` separate. Depending on the stop, the reason can be `target_reached`, `attempt_ceiling`, `time_ceiling`, `no_more_results`, or an access/navigation failure. The 600-second time budget is a soft deadline checked between operations; an in-flight CDP/browser operation can continue past it, so it is not a hard wall-clock guarantee. Already collected results are retained when the run stops early. The CLI prints JSON to stdout and does not save a file. Exit status is 0 when the requested readable target is met, 2 when JSON is returned but the target is short, and 1 when the search fails before returning a result. Do not interpret candidate count as readable count or the target as a guarantee.

Discovery is through Google-indexed results, not Instagram live search. Google can omit, rank, or retain pages unevenly, so the sample may be stale or incomplete. `--recent-days` filters Google's index results; it does not verify publication dates or freshness. Snippets are unverified search context and never count as caption text or keyword matches. Keyword matches use visible text collected from the Instagram page only. A source association that cannot be verified is not counted toward the target. These bounded observations are not platform totals, a representative sample, popularity ranking, real-time feed, or trend measurement.

The local headed browser and reader may encounter unavailable pages or access boundaries. The app cannot verify authorization or whether the browser is logged out. Use only content you are authorized to access. If a login/access wall, challenge, or consent boundary appears, stop; the workflow does not bypass it. A signed-in browser session does not establish authorization. The terminal workflow closes its owned browser tab after completion where cleanup is confirmed.

The Chainlit `extract` flow remains separately capped at five candidates; `collect` does not silently expand that legacy behavior.

The Chainlit `extract` workflow remains separately bounded to at most five candidates. `collect` is the distinct target-based path.

Google's index can be stale or incomplete and may omit, rank, or retain pages unevenly. Extraction failures are reported per candidate; one unavailable page does not discard other candidates that were read. The report shows the readable-page count (for example, `2 of 5 candidates`), each candidate's source and collection status, and the first 1,000 characters of collected post/caption text. A visible post date is shown when available. Google search snippets are labeled as unverified context and are kept separate: snippets do not count as post text and are excluded from keyword matching or draft suggestions. Matching uses only text actually collected from the Instagram page. Results are not an exhaustive search, current trend report, popularity ranking, or representative sample.

The browser search and extraction happen only after you send `extract`; access walls are not bypassed. Handle any browser consent prompt manually on the headed machine. Do not provide credentials in the app or chat.

### Save and compare samples (optional)

The **Search a topic** button asks for any hashtag or phrase, then runs the same `extract` flow. For a simple example, type `finance` (or `#finance`). Saving is separate and opt-in:

1. Send `extract finance` and review the results (or click **Search a topic** and enter `finance`).
2. In the same chat, send `capture finance` to save the sample metadata and aggregate co-occurring hashtag counts. This does not run another search or save post text. Use the same phrase or hashtag, including punctuation such as `#`, for later commands.
3. Later, send `extract finance` again, review that result, then send `capture finance` again.
4. Send `compare finance` to compare the two latest saved samples for that topic.
5. Send `forget finance` to delete all saved samples for that topic.

Only an explicit `capture` persists a snapshot, under `~/.local/share/socialrum-lab/search-snapshots/` on the machine running the app. It stores terms/query, source and version, capture time, candidate URLs, read status, an observed post date when available, and—when posts were readable—up to 20 aggregate co-occurring hashtag counts with the readable-post denominator and a truncation flag. It does not save captions, Google snippets, per-candidate error text, or which hashtag appeared in which post. `compare` makes no new search; it compares the URL sets and aggregate hashtag counts from the two latest eligible captures. It reports URLs seen in both, only in the later sample, or not in the later sample, plus hashtag counts as `count/readable posts` for each sample. Each sample needs at least one readable post.

When a saved top-20 hashtag aggregate was truncated, omitted tags are **unknown**, not zero. If a snapshot is from before aggregates were recorded, its aggregate counts are **unavailable**, not zero. The explicitly searched hashtag is excluded from co-occurrence counts when the query itself is a hashtag. These counts summarize text in the saved samples only; without per-post associations, they cannot show which individual post contained a hashtag.

This is a comparison of two small Google-indexed samples, not a timeline of Instagram posts. A URL first seen later may point to an older post; a URL missing from a later result may simply have been omitted by Google's changing or incomplete index. These samples cannot establish trends, popularity, posting growth, freshness, or platform-wide activity. `forget` deletes the locally saved snapshots matching the normalized topic terms; it does not delete anything from Instagram or Google.

### SSH use

When SocialRum Lab runs on a remote host over SSH, its `cdp` subprocess also runs on that remote host. The usual SSH port forward exposes only the Chainlit web app; it does not make a browser on your laptop available to the remote `cdp` command. For the URL workflow, the remote host needs its own reachable headed browser and `cdp` setup, and the Instagram pages matching the supplied URL host and path must already be open in that browser. If you only have a browser on your laptop, use the manual-paste route below instead. Do not expose Chrome's remote-debugging port over the network.

### Manual public-text route

If visible-page collection is unavailable, send `instagram paste`. Paste up to five lines in this format, then enter one keyword when asked:

```text
https://www.instagram.com/p/EXAMPLE/ | Public caption or other public text you can view
https://www.instagram.com/reel/EXAMPLE/ | Another public excerpt
```

This route analyzes only the text you paste. It does not open the URL, fetch Instagram, or verify that the excerpt matches the linked page. Use only public content you are authorized to share; do not paste private, login-only, or sensitive material. The URL must use HTTPS and an Instagram domain. Each excerpt is bounded to 4,000 characters.

## Official Meta API option

Meta's official Instagram APIs are a separate integration path; this prototype does not use them. They are intended for Instagram professional accounts (Business or Creator) and require a Meta developer app with the relevant login flow and scoped permissions. Signing into Instagram in an ordinary browser does not authorize a developer app: the account owner would separately sign in through Meta's official consent flow and grant that app specific permissions. This prototype requests neither login nor authorization. Never paste Instagram or Meta credentials, access tokens, or app secrets here. See Meta's [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform/overview).

The `ig_hashtag_search` endpoint is for hashtag discovery, not arbitrary keyword search such as “personal finance.” Meta documents a limit of 30 unique hashtags per rolling seven-day period. Access to public content can require the Instagram Public Content Access feature and App Review; Advanced Access and business verification requirements depend on the permissions and app use case. See the [hashtag search reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag-search) and [App Review guidance](https://developers.facebook.com/documentation/instagram-platform/app-review).

Meta's overview and endpoint reference do not appear fully aligned on whether the relevant login configuration is Unified Login or Facebook Login. Confirm the current requirements in the endpoint-specific documentation before designing an integration. Whether Meta would approve access for generic finance-topic research is unconfirmed. No API, subscription, or paid service has been created or enabled for this project.

## What the snapshot means

- A match means the keyword occurs in visible or pasted text. It does not mean the post is popular, representative, new, or trending.
- Creator names are shown only when uniquely verified in the visible post scope; otherwise the report says they are unidentified. Dates are shown only when visible and unambiguous; engagement metrics are not inferred. Manually pasted excerpts do not identify a creator.
- The collector examines only the supplied pages and visible text, with a maximum of five URLs per run. Single-post pages use one fragile, narrowly observed selector; tag pages use visible article elements. Missing or blocked posts are not evidence that no matching posts exist.
- Nothing is saved as a dataset or published. The result is generated in the current chat.

For repeatable analysis or broader coverage, collect observations manually in a CSV you are authorized to use and analyze that file with `upload`. Keep missing metric cells blank; do not invent values.
