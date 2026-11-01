# SocialRum Lab

A local learning prototype inspired by SocialRum's creator-research workflow. It analyzes a synthetic example or a CSV you provide, ranks posts within that dataset, and supports drafting and reviewing content. It is not an official SocialRum product or a reproduction of its private implementation.

## Requirements and local run

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). The project pins Python 3.12 and Chainlit 2.12.0 in its lockfile. Python 3.14.2 caused Chainlit to crash on a live Socket.IO connection in this environment; Python 3.12 was used for the successful local conversation check.

```bash
uv sync --group dev
uv run chainlit run app.py -w --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Binding to loopback keeps the app off the network interface. Chainlit's project has been archived and is no longer receiving security fixes or support ([project status](https://docs.chainlit.io/get-started/project-status)); keep this prototype for trusted local use.

The commands above do not install `cdp`. Optional live-page commands (`collect`, `extract`, and `instagram <keyword>` / `instagram urls`) require the separately installed `cdp` CLI on the `PATH` of the shell that starts the app, plus a headed browser that `cdp` can reach on the same machine. `collect` and `extract` also need network access to Google and accessible Instagram pages. This prototype does not use the Meta API or require a Business/Creator account or Instagram login. Use only content you are authorized to access; the app does not verify authorization or whether the headed browser is logged out. It does not request credentials or log in. Stop if Instagram requires a login. Only one public search (`collect` or `extract`) can run at a time across all chats handled by this app process; another request during that search is rejected, not queued. The synthetic `demo`, CSV upload, `find` link, and manual `instagram paste` route do not require `cdp`.

### Access from another computer over SSH

Run the app on the remote machine with the command above. On your local computer, forward a local port through SSH:

```bash
ssh -N -L 8000:127.0.0.1:8000 <user>@<host>
```

Then open <http://127.0.0.1:8000> on your local computer. If port 8000 is already in use locally, replace the first `8000` with another local port (for example `8001`) and open that port instead. The tunnel forwards only the Chainlit interface. For `extract`, the app's `cdp` process and headed browser must still be available on the remote app host; a browser on the laptop is not forwarded to it. Uploaded data, saved drafts, generated captioned videos, and any Ollama service are on the machine running the app.

## Use the app

Send these commands in the Chainlit chat:

| Command | What it does |
| --- | --- |
| `demo` | Analyze the bundled synthetic example. |
| `upload` | Upload and analyze a CSV. Required columns: `posted_at`, `creator`, `content`. |
| `find <phrase>` | Create a Google search link for public Instagram post/reel URLs. The app does not run this search. |
| `extract <phrase>` | Run a user-initiated Google search in the headed browser and attempt visible text collection from at most five public post/reel candidates; the report summarizes matches/readable posts, readable pages/candidates, and observed dates/readable posts. |
| `collect <phrase> [--target N]` | Run the separate reel-only workflow. Targets 25 readable records by default; accepts 1–50. `--target-readable N` is an equivalent option. May return fewer and reports attempts, readable records, and stop reason. |
| `capture <phrase>` | Opt in to saving sample metadata and aggregate hashtag counts from the latest matching `extract` result; it does not search again or save post text. |
| `compare <phrase>` | Compare URLs and aggregate hashtag counts in the two latest saved samples for the same phrase. |
| `forget <phrase>` | Delete locally saved samples for the same phrase. |
| `instagram <keyword>` / `instagram urls` | Analyze visible text from matching public Instagram pages already open in the headed browser. |
| `instagram paste` | Analyze public excerpts you paste; the app does not open or fetch the URLs. |
| `profile` | Set your name and role for drafting; the profile lasts for the current chat session. |
| `draft 1` | Create a deterministic template draft from ranked post 1. Choose another displayed rank as needed. |
| `draft 1 --model <model-name>` | Explicitly ask the local Ollama service for a draft using a model already installed there. |
| `edit` | Replace the active draft with the complete text you provide. |
| `review` | Set the active draft's local review state to `needs_review`, `reviewed`, or `approved`. This is bookkeeping only. |
| `save` | Save the active draft, its source/provenance, and review state to the local draft library. |
| `list` | List saved drafts and their IDs. |
| `open <id>` | Reopen a saved draft by its 32-character ID. |
| `export` | Download a Markdown brief for the active saved draft, including provenance and review state. Save the draft first. |
| `captions` | Upload a video and your SRT/WebVTT subtitle file for local caption burn-in. |
| `transcribe` | Opt in to local Whisper transcription and receive a rough SRT draft for review. Requires a separately installed CLI and model. |

Nothing is published. A draft starts in `needs_review`; edits and review-state changes remain in the current chat until saved. Use `list` and `open <id>` in a later chat to continue with saved drafts.

## CSV format

The required date is an ISO calendar date (`YYYY-MM-DD`); date-time values are not accepted.

```csv
posted_at,creator,content
2026-10-25,example_creator,"A sample post caption"
```

Optional columns are `platform`, `post_url`, `pillar`, `views`, `likes`, `comments`, `shares`, `saves`, and `followers`. Accepted aliases are `date` or `publish_date` for `posted_at`; `account`, `handle`, or `username` for `creator`; and `caption` or `text` for `content`. Leave unavailable metric cells blank; they remain unknown rather than being treated as zero. CSV uploads are limited to 5 MB.

`data/sample.csv` is synthetic demonstration data, not actual creator content or current trends. Rankings describe patterns among supplied records; they are not a live trend feed and do not predict future performance. The optional Instagram search workflows are `find <phrase>` (Google search link only), `extract <phrase>` (at most five post/reel candidates), and `collect <phrase>` (a separate reel-only readable-record target). `collect` defaults to 25, accepts 1–50, and may return fewer if Google has too few candidates, reads fail, source association is unverified, or a bounded stop is reached. A candidate counts toward its readable target only when visible caption text is nonempty and its association to that reel is verified. Google snippets are unverified context and are excluded from matches; only visible post/caption text is used. Google's index may be stale or incomplete, and individual candidates can fail while others succeed. The terminal `collect` command offers the same target with a maximum of 100 unique candidate attempts and 10 visible result pages, plus a **soft 600-second deadline checked between operations**; an in-flight browser/CDP operation can continue beyond it. Reel-only discovery is the CLI default; `--reels-only` remains accepted, and `--include-posts` opts into a mixed post/reel sample. For example: `uv run python scripts/search_instagram_posts.py '#education' --target-readable 25`. `--max-attempts` (or its compatibility alias `--limit`), `--max-pages`, and `--time-budget-seconds` can lower those bounds/deadline. This is Google-indexed discovery, not Instagram live search; results are not real-time, exhaustive, representative, or a popularity/trend report. The separate `instagram <keyword>` flow reads visible article text from supplied tag pages and uses a narrowly observed caption selector for individual post/reel pages when available. It matches pages already open in a headed browser by Instagram host and path; query strings and fragments do not affect tab matching, and `/explore/tags/<slug>` can match its `/popular/<slug>` redirect. One historical post permalink succeeded in a manual test; the direct finance hashtag page still returned a login wall. These best-effort paths do not provide current-trend or platform-wide coverage and do not publish posts. After `extract finance` (or another phrase), use `capture finance` to opt in to saving local search/sample metadata and aggregate co-occurring hashtag counts. Repeat the extraction and capture later, then use `compare finance`; use `forget finance` to delete the saved samples. The comparison is only between two bounded Google-indexed URL samples. See [Instagram workflow](docs/instagram-workflow.md) for the exact steps, storage details, and limitations.

To try a topic, click **Search a topic** and enter a phrase such as `finance` or a hashtag such as `#finance`. Or send `extract finance`, then `capture finance` to save this sample. Later repeat `extract finance` and `capture finance`, then send `compare finance`. Send `forget finance` to remove the local samples for that topic. Capture is opt-in: it stores search metadata and, when posts were readable, up to 20 aggregate co-occurring hashtag counts with a readable-post denominator under `~/.local/share/socialrum-lab/search-snapshots/`. It does not save captions, Google snippets, or per-post hashtag associations. Compare reports URLs recurring or changing between samples and hashtag counts as `count/readable posts`; tags omitted from a truncated aggregate are unknown, while aggregates missing from older snapshots are unavailable, not zero. This sample comparison does not measure Instagram trends, popularity, or posting growth.

### Retrospective publication-cohort comparison

Analysis also compares post counts for each content-pillar label across two adjacent historical windows. Each window is seven inclusive calendar days: the recent window ends on the newest `posted_at` date in the supplied records, and the baseline is the immediately preceding seven days. Exact duplicate observations are counted once. A comparison is marked comparable only when each window contains at least three posts from at least two distinct creators; zero-post baselines and windows below those minimums are labeled insufficient instead of receiving a count change. For comparable windows, the result is the raw difference in post counts (recent minus baseline).

This is a retrospective count comparison over supplied records, not live trend velocity or a growth rate. The optional `platform` field does not segment the comparison or establish platform coverage; no platform-wide completeness is measured. Content-pillar labels are used as topic proxies, so results depend on the labels and date coverage in your CSV.

## Draft storage and privacy

Saved draft records live under `~/.local/share/socialrum-lab/drafts/` in the home directory of the account running the app. Markdown exports are kept in its `exports/` subdirectory. The directory is shared by chats handled by that app process, rather than being separated by Chainlit user or chat. It is intended for a single trusted local user, not a multi-user deployment. Draft records, exports, and transcript downloads are stored locally with owner-only filesystem permissions, but are not encrypted; anyone with access to the app's operating-system account can access them. Chat profiles and analysis state remain in the current Chainlit session; only drafts you save are kept in the draft library. Captioned video outputs are written to the project's `outputs/` directory. Transcription SRT files are stored under the draft library's `transcripts/` subdirectory. Both are available for download from the chat.

## Optional local Ollama drafting

Template drafting works without a model. To request model generation, install and start [Ollama](https://ollama.com/) on the same machine as the app and ensure the named model is already installed there. Then use `draft <rank> --model <installed-model-name>`. The app sends the request to `http://localhost:11434`; this request has a 30-second timeout. Real model inference has not been verified in this project. No model is bundled or downloaded by the app. Connection, timeout, and model errors are reported; an explicit model request never silently falls back to a template. The locally provided post text is unverified input, and any generated copy should be reviewed before use.

## Caption rendering

The `captions` command burns a subtitle file you provide into a video using the locally installed `ffmpeg` executable. It accepts MP4, MOV, or MKV video up to 500 MB and UTF-8 SRT or WebVTT subtitles up to 5 MB. This command only burns supplied captions; transcription is a separate opt-in command. Install ffmpeg separately; it is not installed by `uv sync`.

## Optional local transcription setup

The `transcribe` command uses a local `whisper.cpp` executable and model. It does not install software, download a model, or contact a cloud service while the app runs. To build and install the pinned CLI and English `tiny.en` model, run the setup script yourself from the repository root:

```bash
bash scripts/setup-whisper.sh
```

This one-time setup requires `git`, `cmake`, `curl`, `sha256sum`, and a C++ compiler, plus internet access to fetch the pinned source and model (about 78 MB). It installs under `~/.local/share/socialrum-lab/whisper/` and prints the environment variable exports. See [docs/whisper-setup.md](docs/whisper-setup.md) for prerequisites, integrity checks, and setup options. Validation completed in an isolated clean install: the pinned build and install succeeded, and a repeat run correctly refused to replace the existing install. The app was also exercised through a real UI transcription flow using the default runtime paths, including SRT download.

By default the app looks for:

- `SOCIALRUM_WHISPER_CLI`: `~/.local/share/socialrum-lab/whisper/bin/whisper-cli`
- `SOCIALRUM_WHISPER_MODEL`: `~/.local/share/socialrum-lab/whisper/models/ggml-tiny.en.bin`

Set either variable in the shell that starts the app only if your executable or model is at a different path. If the files are absent, `transcribe` reports that local transcription is not configured. Runtime use never downloads missing files.

The command accepts audio or video files in WAV, MP3, M4A, AAC, FLAC, OGG, OPUS, MP4, MOV, MKV, or WebM format, up to 100 MB and 10 minutes. `ffmpeg` and `ffprobe` must also be installed locally. The app processes the supplied media locally and stores the generated SRT under `~/.local/share/socialrum-lab/drafts/transcripts/` for download; external processing commands have a 120-second timeout. Output is a rough machine draft: `tiny.en` is English-focused, and every word and timestamp must be reviewed and corrected. A synthetic CLI inference also completed, but its output was rough; in one short synthetic sentence, the expected phrase was not transcribed correctly. The successful UI flow validates local wiring and download, not recognition quality. These checks are not an accuracy benchmark, and no general accuracy claim is established, including for accents, noisy audio, multiple speakers, other models, or non-English speech.

## Checks

```bash
uv run python -m pytest -q
```

This is a local learning project and uses no paid APIs, hosted infrastructure, or billable resources. Avoid using sensitive material on shared machines.
