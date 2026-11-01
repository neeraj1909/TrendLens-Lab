# SocialRum Lab — investor live demo guide

## Live quick reference — public-post discovery first

This walkthrough starts with a user-requested search for the phrase `finance`. It demonstrates a bounded attempt to find public Instagram posts through Google’s visible index and read candidate pages. Results vary; this is not a live Instagram feed, market benchmark, or production SocialRum integration.

### Check prerequisites before presenting

- The app runs on the app-host machine with Python 3.12 and project dependencies installed. For first-time setup, follow the README before meeting day; do not rely on network installs during the presentation.
- For `extract`, the `cdp` CLI must be installed and on the app process's `PATH`, and a reachable headed browser must be available on the **same app-host machine**. The app needs internet access to Google and the public Instagram pages. `demo` and CSV analysis do not need `cdp`.
- This prototype uses no Meta API and requires no Business/Creator account setup or Instagram login. Use only public content you are authorized to access, consistent with [Instagram's Terms of Use](https://help.instagram.com/581066165581870). The app does not verify permission, check whether the headed browser is logged out, or log in. If Instagram requires a login, stop; do not supply credentials in the app or chat. Handle optional cookie consent manually in the headed browser.
- If the app is remote, the browser and `cdp` still run on that remote app host. The SSH tunnel carries only the Chainlit interface to the laptop; it does not forward the laptop's browser to `cdp`.

### Demo A — search public Instagram posts for a topic (about 3 minutes)

1. If needed, start the app on the app host from the repository root:

   ```bash
   uv run chainlit run app.py -w --host 127.0.0.1 --port 8000
   ```

2. From the presentation computer, open the already-established SSH tunnel and go to its forwarded `http://127.0.0.1:8000` page. The repository does not record the actual SSH account or hostname; use the known address and do not guess one. If the tunnel is down:

   ```bash
   ssh -N -L 8000:127.0.0.1:8000 <user>@<app-host>
   ```

   Keep the tunnel terminal open. Open `http://127.0.0.1:8000` on the laptop. If the app and presentation browser are on the same machine, skip SSH and open the local address directly.

3. Click **Search a topic**, enter `finance`, and submit. Or send the command `extract finance`. A bare `finance` message does not start a search. The app opens a Google search and tries to collect visible text from up to five public Instagram post/reel candidates in the app host's headed browser.
4. Read the evidence summary in order: it names the Google query and search time, counts **visible-text matches / readable posts**, **readable post pages / candidates**, and **observed dates / readable posts**. Then inspect the individual candidate entries: each gives a source link and collection status; readable candidates show collected post/caption text, with any Google snippet labeled unverified. Dates and creator names appear only when available and verified from the visible page. A text match and editable draft suggestion appear only when the keyword occurs in collected post text. The suggestion is copyable wording to edit yourself; it is not saved as an app draft. Some candidates can fail while others succeed. Zero candidates, zero readable pages, or zero visible-text matches are valid results.

**What to say:** “I asked the prototype to search for `finance`. First it tells us how many candidate pages it could read, how many readable posts actually mention the phrase, and how many have a visible date. Then we can inspect the source links and the text behind any match. This is a small sample from Google’s index, not evidence that finance content is trending or popular.”

**Set expectations:** the index may be stale or incomplete, post pages can be blocked, and matching/extraction depends on Instagram's current page markup. A suggested angle is only a draft aid grounded in visible text; verify claims yourself. Do not present the candidate list, hashtag co-occurrence counts, or sample comparison as exhaustive, representative, current-trend, or platform-wide evidence.

5. Optional: after reviewing the search, send `capture finance` in the same chat to opt in to saving local sample metadata and aggregate hashtag counts. For a later comparison, repeat `extract finance` and `capture finance`, then send `compare finance`. `forget finance` deletes the saved samples. Comparison reports bounded sample overlap and aggregate counts, not trend growth or popularity; do not promise a comparison during a first-time live presentation.

### Fallback — clearly labeled synthetic demo (about 3 minutes)

If `cdp` is unavailable, the search is blocked, permissions are unclear, or the result is empty, use the clearly labeled fictional example in the fallback walkthrough below. Do not present it as evidence about real Instagram content.

**Purpose:** show a bounded public-post discovery attempt first when prerequisites and permissions are in place, with the deterministic synthetic workflow as a reliable fallback. Neither path demonstrates a production SocialRum integration.

## Before the meeting

1. On the machine hosting the app, confirm the app is already running as described in `README.md` (Python 3.12, Chainlit, loopback `127.0.0.1:8000`). For the real-post demo, ask the host operator to confirm `cdp` is on the app process's `PATH` and a headed browser is reachable there. Avoid restarting a working instance just before the demo.
2. The existing remote-access setup uses an SSH local-forward tunnel. Check that the already-open tunnel terminal/session is still connected. If it is not, use the README command from the presentation computer, substituting the real account and host:

   ```bash
   ssh -N -L 8000:127.0.0.1:8000 <user>@<host>
   ```

   Keep that terminal open; it is expected not to print progress. Open `http://127.0.0.1:8000` on the presentation computer. If local port 8000 is occupied, forward a different local port (for example `8001:127.0.0.1:8000`) and open `http://127.0.0.1:8001`. Do not start a second tunnel if the existing one is working.
3. Confirm the welcome message, Quick guide panel, and **Search a topic** / **Run synthetic example** buttons are visible. Keep this to a trusted machine and audience: the draft library is local to the app host and is not separated by Chainlit user/chat.
4. Have the app host’s `data/sample.csv` ready as an optional synthetic upload example. Do not use a visitor/customer CSV in the presentation unless its owner explicitly authorized this use and it has been reviewed and sanitized for confidential or personal information. Do not fabricate a CSV or metric values for the pitch.
5. Use a fresh chat/session if you want the first-time welcome and a clean draft. The bundled analysis is reproducible; a previously saved draft may appear if you use `list`.

## Fallback walkthrough — bundled synthetic signal to editable draft (about 3 minutes)

### 1. Load the example

Click the welcome card’s **Run synthetic example** button, or send this exact message:

```text
demo
```

Expected: a `Dataset overview` for **20 synthetic records** and a `First signal to inspect` showing `@maya.makes · Education` ranked first. It should report **17.25% engagement** against a **5.30% peer median**, **3.25x lift**, and **3 peers**. The underlying ranked post is dated 2026-10-22: `[DEMO] The one-page launch checklist readers saved`. A second, genuinely different ranked example is `@maya.makes · Community`, `[DEMO] Three community questions, answered` (2026-10-28), at **14.89%** vs **7.71%** with **3 peers** (**1.93x**). Send `draft 1` for the Education example or `draft 2` to create a draft from the Community example. Explain that engagement is observed likes + comments + shares + saves divided by views, and the comparison uses the same creator, pillar, and available interaction metrics. The report labels the result retrospective and dataset-scoped.

Also point out the `Retrospective publication-cohort comparison`: for the current fixture it compares the inclusive baseline window **2026-10-15 to 2026-10-21** against the recent window **2026-10-22 to 2026-10-28**. All three pillars meet the minimums in this synthetic file: Education **3→3 (0)** posts, Behind the scenes **3→4 (+1)**, and Community **4→3 (-1)**, with two creators in each window. This makes a useful second example: different pillars show stable, up, and down raw counts. These are counts from this fixture; they do not establish a live or market-wide trend.

**Talk track:** “This starts with records the user supplied. The first signal is inspectable: we can see the post, its same-creator/pillar peer median, and the peer count. It is a historical within-file comparison, not a prediction.”

### 2. Turn that post into a draft

Send:

```text
draft 1
```

If asked for profile, enter this exact example:

```text
Neeraj | creator educator
```

Expected: the app labels generation as a **deterministic local template**, marks the draft **needs review**, shows its source/provenance, and displays editable copy. The copy is a generic template seeded with the selected topic/profile; it is not AI model output. Nothing is published.

**Talk track:** “The signal is a starting point for a human workflow. This default path is deterministic and local, so I can show the draft without depending on a paid model or network service.”

### 3. Demonstrate human control and local persistence (optional)

Send `edit`; when prompted, paste a complete replacement, for example:

```text
What makes a practical checklist useful to your audience? Share one step you would keep and one you would change.
```

Expected: the full replacement appears in the active draft; the app says it remains in this session until saved. Then send `review`, and enter `reviewed` when prompted. Expected: local bookkeeping state changes; it does not publish or verify the content. Send `save`; expected: a 32-character draft ID and “Saved locally.” Capture that ID from the app, then send `export`. Expected: a downloadable Markdown brief with source/provenance and review state. `list` and `open <id>` can show the saved item later.

**Talk track:** “The useful product boundary here is reviewability: the source is retained, edits are explicit, review status is bookkeeping, and export is a local handoff artifact. There is no publishing integration.”

## Optional follow-up — a visitor’s own CSV, or the deliberately sparse case (about 2 minutes)

This is a genuinely different input path from the bundled fixture: it shows whether the workflow accepts records a user brings, rather than implying the synthetic sample is a live feed.

1. Send:

   ```text
   upload
   ```

2. At the file picker, choose the visitor’s approved CSV. Required columns are `posted_at`, `creator`, and `content`; dates must be ISO calendar dates (`YYYY-MM-DD`). Optional columns include `pillar`, `views`, `likes`, `comments`, `shares`, and `saves`. Blank metric cells stay unknown. Limit: 5 MB.
3. Expected: an analysis of that file’s records, available creator/pillar summaries, and ranked posts only where comparable data exists. If the CSV lacks enough same-creator/same-pillar posts with comparable observed metrics, expected behavior is **“No comparable outlier posts were found”** or no ranked signal; this is an honest sparse-data outcome, not a defect to conceal. The temporal comparison may be marked insufficient when either window lacks the minimum three posts and two creators.
4. If there is no visitor file with explicit permission for this demonstration and no personal/confidential data, skip this section. You may use `data/sample.csv` as an alternate upload route, but state clearly that it is still bundled synthetic data. The uploaded fixture should produce the same two ranked examples; it is a different command path, not independent or customer-supplied evidence.

**Talk track:** “The same analysis can run over an uploaded export, but interpretation is bounded by its coverage and labels. Missing values are not treated as zero, sparse samples do not get a made-up lift, and these rankings are not causal.”

Do not promise a particular result for a visitor CSV before seeing its actual contents. If it is malformed, the app reports a validation error; fix the source file outside the demo or return to `demo` rather than editing the project during the meeting.

## Optional extensions (only if preflighted)

- **Local Ollama draft:** `draft 1 --model <installed-model-name>` only if Ollama is already running on the app host and that exact model is installed. This calls `localhost:11434`; it is not needed for the core demo. Real inference is not validated in this project. Connection/model failures are reported; explicit model requests do not silently fall back to the template. Do not present the default template as model-generated copy.
- **Captions:** `captions` accepts a video plus a supplied SRT/WebVTT file and uses local `ffmpeg` to burn captions into a downloadable output. It does not transcribe. Use only if known-good sample assets and local `ffmpeg` were prepared beforehand.
- **Transcription:** `transcribe` requires the separately installed local Whisper CLI/model and local `ffmpeg`/`ffprobe`. It produces a rough SRT that requires review. The README records a synthetic smoke run that mistranscribed a simple expected phrase; accuracy is not established. Skip this in an investor demo unless the roughness is itself relevant to the discussion.

## Recovery notes

- **Chat page unavailable:** check that the host app is still running on loopback port 8000 and that the existing SSH tunnel is connected. Reopen the forwarded URL. If port 8000 is occupied on the presenter machine, use a different *local* forward port as above.
- **`demo` says the bundled CSV is missing:** stop the walkthrough and restore the project’s bundled `data/sample.csv` before retrying; do not improvise data in the application.
- **A command says load data first:** send `demo` or `upload`, then retry the intended command.
- **`draft 1` says choose a ranked post:** the current analysis has no rank 1 (usually too few comparable observations). Use the bundled example for the draft sequence, or explain the sparse-data outcome. Do not claim the visitor’s file produced a signal it did not produce.
- **Profile prompt is rejected:** re-send `profile`, then enter two non-empty values separated by ` | `, for example `Neeraj | creator educator`.
- **Saved draft conflict or local write failure:** preserve the in-session text. For a conflict, use `open <id>` to load the current saved version before continuing; for a storage error, stop the persistence portion and continue explaining the in-session draft. Do not claim it was saved.
- **Optional local model or media tool fails:** skip that extension and return to the core template flow. Never imply cloud fallback or successful processing.
- **An extraction is already running:** the app allows only one `extract` at a time across its chats. The new request is rejected, not queued; wait for the active search to finish, then send the command again.
- **Tunnel interruption mid-demo:** reconnect the tunnel, reload the page, and if session state is lost, send `demo` again. A profile and unsaved draft belong to the current chat session; saved drafts are local on the app host.

## Claims to keep precise

- This is a local, single-user learning prototype; it is not an official SocialRum product or a reproduction of private implementation.
- The real-data demo is a user-triggered search of Google's visible index followed by an attempt to read up to five public Instagram post/reel pages. It may return partial, stale, blocked, or empty results and does not establish platform-wide coverage, current trends, or popularity. The separate synthetic fallback uses only the explicitly fictional bundled sample. Nothing is published.
- Rankings are descriptive comparisons within the supplied records. An engagement rate is shown only when views and at least one interaction metric are observed; different interaction coverage is not mixed into peer comparisons. Results do not establish causality.
- The historical pillar comparison uses adjacent inclusive seven-day windows anchored at the newest supplied date, with a minimum sample of three posts from two creators in each window. It is a raw count difference when comparable, not velocity or growth rate.
- Default drafting is a deterministic template. Optional Ollama is local and unverified; no model is bundled. No paid APIs, hosted infrastructure, or billable resources are part of this demo.
- Saved drafts, exports, and optional media outputs are local to the app-host machine. Draft storage is not encrypted or multi-user isolated; do not use sensitive content on a shared machine.

## Source of truth checked

This walkthrough is grounded in `README.md`, `chainlit.md`, `app.py`, `socialrum/search_connector.py`, `socialrum/instagram.py`, `socialrum/snapshots.py`, `socialrum/data.py`, `socialrum/drafts.py`, `socialrum/store.py`, and `data/sample.csv`. UI values above reflect the current 20-row fixture and ranking/comparison rules; customer-file outcomes are intentionally described conditionally. The fixture contains only synthetic `[DEMO]` observations, with small peer pools; treat the fallback as a workflow illustration, not evidence of market performance.
