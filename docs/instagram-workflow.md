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

For user-initiated discovery, send `extract #finance` or `extract personal finance`. This opens a Google search in the headed browser, reads visible result links, and attempts to collect visible text from at most five public Instagram post/reel candidates. It does not query Instagram's live search. The existing `find #finance` or `find personal finance` command remains the link-only option: it creates a Google search link for you to open and does not run the search or read results.

#### Terminal reel search (up to 20)

To search Google for reel candidates directly from a terminal on the machine
with the headed browser and `cdp` on `PATH`, use:

```bash
python3 scripts/search_instagram_posts.py '#education' --reels-only --recent-days 30 --limit 20
```

Replace `#education` with a hashtag or phrase, such as `'personal finance'`. The `--reels-only` flag limits discovery to reels; without it, terminal searches use the default mixed post/reel mode. `--recent-days 30` applies a Google index date filter for approximately the last 30 days; it is not verified Instagram publication-date filtering. The command attempts to read up to 20 reel candidates. Google may return fewer than 20 candidates, and its index filter cannot guarantee publication dates, recency, or that results are the latest reels. When an Instagram `/reel/<id>` URL redirects to `/reels/<id>`, the fallback reader uses the first visible reel/video card. That heuristic assumes the first visible video is the requested reel and cannot guarantee source matching; returned captions may be truncated. A visible or indexed date is not verified as the reel's publication date. The command prints JSON to the terminal without saving a file, checks for access walls, stops when a boundary is encountered, and closes its own browser tab. Individual candidates can be unavailable. The exit code is 0 for a completed run, 1 for a failure before results, and 2 when a run stops early with partial JSON. The script does not verify authorization, browser sign-in state, or whether a candidate is publicly accessible. Follow Meta's policies and only collect content you are permitted to access this way. A signed-in browser does not establish that a page is public or authorize automated collection. If an access wall appears, the script stops; it does not bypass the wall. The JSON may contain visible post text and unverified Google snippets; handle it accordingly. Its `sample_summary` separates the number of Google-result candidates from the number successfully read with nonempty visible text, then reports literal visible-text matches as a count out of readable text. Matching uses the existing case-insensitive, whole-word phrase rules; for a hashtag query such as `#education`, the literal `#education` must appear in collected post text, and plain `education` alone does not match. Snippets and URLs do not contribute to this count. A snippet-only hit is not evidence that a reel was read or matched. These are bounded sample counts, not platform totals, a real-time Instagram search, or trend measurements.

The Chainlit `extract` workflow remains separately bounded to at most five candidates. This terminal command does not change that app limit.

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
