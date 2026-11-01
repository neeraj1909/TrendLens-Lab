# Research note: extracting keyword and hashtag trends

**Prepared:** 2026-11-01  
**Project:** SocialRum Lab  
**Scope:** What the current Instagram workflow can establish, what evidence is needed to call something a trend, and a practical zero-additional-cost path forward.

## Executive assessment

The current `extract <phrase>` workflow can discover a small set of Instagram post/reel URLs through Google and attempt to read visible post text. In a user-triggered live validation, two `finance` samples about six minutes apart each had 5 of 5 candidate pages readable, and all 5 candidate URLs recurred between the samples. The separate `extract #finance` path was also exercised; creator/date metadata was available on some readable posts but not consistently. These are useful smoke-test results for the path. They do not show trend volume, growth, Instagram-wide reach, representativeness, or whether posts were recent enough to be a trend.

A trend claim needs a defined population, a repeatable sampling frame, time-stamped observations, and a comparable signal over multiple time windows. The current workflow has none of the necessary platform-wide denominator or reach/engagement time series. Google-indexed candidate URLs are discovery leads, not an Instagram feed. The least misleading product wording today is **“bounded sample of visible public posts mentioning this keyword/hashtag”** or **“sample changed between two Google-indexed searches.”**

For Instagram hashtag-level research, Meta's official API is the only route in the options reviewed that is designed to return public hashtag media programmatically. It is restricted: it requires an authorized professional-account/app setup and relevant access approval; Meta documents a limit of 30 unique hashtag searches per rolling seven days. It is hashtag discovery, not arbitrary phrase search. Approval for this product's use is unconfirmed. Do not build around an assumed approval. An API can produce a bounded official sample, but it still would not by itself prove trend velocity unless stable time-series fields and coverage are measured.

**Recommendation:** Keep the existing Google-indexed route as explicitly bounded discovery; do not label its output “trending.” For an immediate no-cost learning-project path, develop an opt-in, provenance-preserving panel/CSV workflow with fixed cadence and transparent sampling. Separately, a future spike may verify whether Meta API access is both available and approved before any integration work. Use Google Trends only as a second, search-interest signal, never as a proxy for Instagram usage.

## What was checked

### Repository evidence

The current product contract and constraints are documented in [`docs/instagram-workflow.md`](instagram-workflow.md), [`README.md`](../README.md), and implemented in `socialrum/search_connector.py`, `socialrum/keyword_insights.py`, `socialrum/snapshots.py`, and `app.py`.

- `extract` runs a user-initiated Google query, takes up to five public post/reel candidates, then attempts to collect visible text from each Instagram page. Google snippet text is explicitly excluded from keyword matches and draft suggestions. Candidate failures are surfaced individually.
- `capture` is opt-in and saves metadata (terms, candidates, time, read status, and observed dates) plus bounded aggregate co-occurring hashtag counts when readable post text exists. It does not save captions, snippets, or per-post hashtag associations. `compare` compares URL sets and aggregate co-occurring hashtag counts from two captured samples. URL set difference is not a count of new posts in the period; indexing changes, ranking, and candidate limits can change the set. Aggregate hashtag counts describe only the readable posts in each sample.
- The separate `instagram <keyword>` route reads pages already open in a headed browser. The existing project doc records one successful historical post selector path and a finance hashtag login wall. It also documents fragility of the observed selector and the cap on visible articles read.
- Local CSV analysis is useful for supplied, authorized records and can compare counts in adjacent date windows, but the implementation labels this retrospective comparison rather than live velocity. The sample CSV is synthetic, not creator data.

### Latest live bounded extraction observation (2026-11-01)

| Evidence | Result | Interpretation |
|---|---|---|
| User-triggered `extract #finance` and `extract finance` paths | Both exercised against live Google-indexed discovery and visible Instagram pages | Confirms those paths can return and read candidate posts in this run; it does not establish completeness. |
| Two genuine captured samples, about six minutes apart | Each had 5 candidates and 5 readable pages; all 5 candidate URLs recurred between samples | Demonstrates sample stability for these two captures only, not posting growth or platform activity. |
| Post metadata | Creator and/or date was available on some readable posts, not consistently | Missing metadata is unknown, not evidence that the author/date does not exist. |
| Co-occurring hashtags | Hashtag counts were calculated from readable post text; the searched hashtag is excluded when applicable | Counts are within-sample post counts only. They do not estimate hashtag prevalence outside these five readable posts. |
| Opt-in capture | Saved sample metadata and aggregate co-occurring hashtag counts; no captions, snippets, or post-to-hashtag associations | Supports later bounded comparison while minimizing stored text. |

This is a small operational check comprising two captures, not a trend study. Candidate composition and read success can vary between runs. The five-candidate cap and Google index mean this is not a probability sample. Five recurring URLs over roughly six minutes are weak evidence about short-interval sample stability, not evidence of growing or stable Instagram activity. No raw caption text is reproduced here. A publicly opened hashtag page separately reached a login wall; do not bypass it. These observations are not estimates of platform coverage or extraction success rates.

### Research method and evidence boundary

This note checked the supplied primary Meta and Google documentation, Instagram's Terms page, and project-owned workflow documentation. The latest live platform observations came from user-triggered extraction and two opt-in sample captures. **A headed Google SERP research run was attempted under an exclusive CDP lease, but its SERP summary artifact was empty and source-page extraction was incomplete.** This is therefore a targeted primary-source synthesis, not a completed headed-browser research pass. Google snippets and search previews are not used as evidence. The Instagram Terms page returned a rate-limit response in this research pass; the project's existing documentation links that Terms page, and Meta's own scraping article states that automated collection without permission violates its terms. Recheck the current Terms and endpoint requirements before implementation.

## What counts as a trend?

A useful operational definition is: **a measurable increase in a specified signal, for a defined population and geography, over a stated time interval, compared with a baseline, with known collection coverage.** Before showing a “trend,” specify:

1. **Signal:** e.g. number of posts containing a hashtag, search interest, or post-level engagement. These measure different things and must not be mixed.
2. **Population and coverage:** Instagram public hashtag media available to an approved API client, a selected creator panel, Google-indexed Instagram URLs, or Google searches. Name exactly which.
3. **Time and timezone:** capture timestamps in UTC; define interval, such as daily, and the period used for baseline (for example, prior 28 complete days).
4. **Sampling:** keep collection rules, query variants, geography/language, source and limits stable. Record candidate counts, readable counts and missingness.
5. **Comparison:** compare like-for-like windows and normalize where appropriate. A raw count from a different search query, candidate cap, or time span is not comparable.
6. **Uncertainty:** show sample size, unavailable fraction, and a caveat when the source is an incomplete or changing index. Avoid “zero activity” when no matching result was observed.

A sensible first release can report **observations and sample change**, not a predictive score. A percentage change is only defensible when there is a stable countable cohort and complete/known coverage; the present Google URL samples do not meet that condition.

## Evidence and its limits

### Instagram public hashtag and keyword discovery

Meta's official docs describe hashtag lookup followed by media retrieval: `GET /ig_hashtag_search?user_id=<IG_USER_ID>&q=<tag>` yields a hashtag ID, then `/<hashtag_id>/top_media` or `/recent_media` returns associated media. This is hashtag search, not arbitrary phrase search: `finance` is a hashtag query; `personal finance` as a phrase is not. See Meta's [IG Hashtag Search](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag-search), [top media](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag/top-media), and [recent media](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag/recent-media) references.

**Authentication documentation conflict (unresolved):** Meta's current [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform/overview) marks hashtag search as supported with Unified Login and Facebook Login, but unsupported with Instagram Login. It recommends Unified Login for new integrations; that setup does not require a Facebook Page, but it does require a business portfolio and uses Facebook Login for Business. However, the endpoint-specific hashtag references still label these endpoints as available for Facebook Login/Facebook Login for Business and specify a Facebook User access token whose user has Page tasks on the connected Page. The [Unified Login permissions page](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/permissions) says hashtag search is supported and uses the `instagram_*` permission family, while [Unified Login setup](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started) describes a Business Integration System User token and no Page requirement. Meta's [Instagram API App Review page](https://developers.facebook.com/documentation/instagram-platform/app-review) still lists Facebook Login and Instagram Login, not Unified Login. These pages do not reconcile the endpoint's token/Page requirements with Unified Login's support claim. **Do not assume a Unified Login token works for these endpoint calls; confirm with Meta or a minimal authorized API request before choosing an integration.**

**Permissions and review boundary:** The endpoint references list the `Instagram Public Content Access` feature and `instagram_basic`. For Facebook Login, if the token user's Page role was granted through Business Manager, the hashtag references require one of `ads_management`, `business_management`, or `pages_read_engagement`. Meta says Standard Access is for app-role users/owned or managed accounts; Advanced Access for users without an app role requires App Review and Business Verification ([overview](https://developers.facebook.com/documentation/instagram-platform/overview), [App Review](https://developers.facebook.com/documentation/instagram-platform/app-review)). The endpoint docs explicitly name the Public Content Access feature. How its feature-level approval is applied to Unified Login is not clear in Meta's current docs.

**Quota and media coverage:** Meta limits each IG user/account to 30 unique hashtags in a rolling seven-day window. The first query consumes a slot; repeating the same tag within that initial window does not reset its timer. Calls to `top_media` or `recent_media` count too, even when the hashtag ID is already known. The [recently searched hashtags edge](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/recently_searched_hashtags) reports IDs currently consuming slots. `recent_media` returns only public photos/videos published within 24 hours of the request, may not be chronological, excludes promoted/boosted/ads media, and paginates at up to 50 per page. `top_media` returns popular public photos/videos (popularity uses views and viewer interactions), excludes ads, and also paginates at up to 50 per page. Neither reference permits requesting the author's `username`. Documented media fields include `id`, `caption`, `media_type` (`IMAGE`, `VIDEO`, `CAROUSEL_ALBUM`), `permalink`, `timestamp`, `comments_count`, `like_count` (omitted when hidden), `media_url` (not for albums and omitted for some copyrighted/licensed-audio videos or reels with downloads disabled), and `children` for albums. These hashtag edges document photos/videos, not Story media. Meta does not promise a real-time feed, delivery latency, or completeness; describe this as a bounded lookup over eligible media, not as real-time monitoring. These constraints are specified in the [hashtag references](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag) and the [quota edge](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/recently_searched_hashtags).

**Allowed-use boundary:** Meta's [Instagram Public Content Access feature description](https://developers.facebook.com/documentation/development/features-reference#reference-INSTAGRAM_PUBLIC_CONTENT_ACCESS) names campaign-content discovery, public sentiment around a brand, customer support, contest entrants, and understanding/managing an audience or content strategy. It also describes analytics for app improvement/marketing/advertising when information is aggregated and de-identified or anonymized so it cannot be re-identified. Generic financial-market or ticker trend analysis is **not explicitly named** in these allowed-use examples. A campaign or sentiment use tied to a specific finance brand may fit the literal examples, but whether generic finance research qualifies is not settled by the docs; do not claim it is approved or categorically prohibited.

**Inference:** Even if the app, use case, login method, permissions, and feature access are accepted, the result set is not automatically a platform-wide trend statistic. A 24-hour eligibility window, top ranking, paging cap, excluded media, and lack of author usernames constrain the observable population. The API references do not provide a total-post denominator or a real-time guarantee. Do not infer total hashtag volume, growth, or representative sentiment from fetched pages.

Meta introduced the [Meta Content Library and API](https://about.fb.com/news/2023/11/new-tools-to-support-independent-research/) for public-interest researchers, with near-real-time public content and engagement details described for Facebook and for Instagram creator/business accounts. The announcement says these tools are intended for researchers; it is not a general-purpose, automatically available developer API for this app. Eligibility, access conditions and current product coverage require separate verification. Do not assume it is an available fallback for a consumer product.

### Google as a discovery index

Google explains that it discovers, crawls and indexes pages algorithmically; it does not crawl every discovered page, and pages can be unavailable without login ([How Google Search works](https://developers.google.com/search/docs/fundamentals/how-search-works)). Therefore a Google result set is a changing index sample, not a census of Instagram posts. The SocialRum implementation correctly treats Google text as unverified context and matches only text read from the destination page.

Meta's [scraping explanation](https://about.fb.com/news/2021/04/how-we-combat-scraping/) explicitly distinguishes authorized crawling from automated collection without permission and says automation without Meta permission violates its terms. Instagram's [Terms of Use](https://help.instagram.com/581066165581870) is the controlling current document; it was not readable in this pass because of a 429 response. Do not treat public visibility or a successful browser read as permission for automated collection. Avoid login automation, hidden-state inspection, request replay, anti-bot evasion, or bypassing access walls.

### Search interest is a different signal

[Google Trends' methodology FAQ](https://support.google.com/trends/answer/4365533?hl=en) says its data is sampled, anonymized, aggregated and normalized to a 0–100 scale relative to total searches for the selected geography/time; low-volume terms can appear as zero, and privacy noise can create apparent spikes for low-volume queries. Google itself says a spike is not a poll or proof that a topic is popular. Use Trends for **Google search interest**, not Instagram post volume or hashtag use. Compare appropriate terms/topics and preserve location/time window when exporting.

**Inference:** Correlation between Google search interest and Instagram conversation may exist for some topics but cannot be assumed. If the product eventually combines them, present two separate series and require validation against a third source or manually reviewed sample.

## Options compared

| Route | What it measures | Access / cost | Strength | Main limitation | Fit now |
|---|---|---|---|---|---|
| Current Google-indexed extraction | At most five candidate Instagram pages; visible text/date where available | Existing local headed browser; zero incremental service spend | Fast to demonstrate a user-initiated workflow; individual failures visible | Stale/incomplete index, unknown coverage, selection bias, small sample; not trend velocity | Keep as discovery/smoke test with strict wording |
| Official Instagram Graph API hashtag search | Bounded public media associated with a hashtag | Meta app, authorized professional account, scoped consent and access review; no app approval is guaranteed | Authorized API contract; potentially more repeatable than manual browser collection | Hashtag only, query budget, access gates, returned media is bounded, approvals/product suitability unknown | Investigate only after validating eligibility and use case; do not promise |
| Meta Content Library/API | Public content for eligible independent research | Research eligibility and access process; conditions need verification | A research-oriented access channel and documented near-real-time public content | Not a general product API; Instagram scope/eligibility must be confirmed | Alternative only for qualifying research |
| Google Trends | Relative Google Search interest, not social posts | Public UI/export; additional cloud resources unnecessary | Gives regional/time-bounded search-interest context | Sampling, normalization, noise, low-volume zeros; not Instagram usage | Useful contextual corroboration, labeled separately |
| Opt-in manual/panel CSV | Records a consenting researcher/creator can lawfully provide | Local files and project code; zero incremental service spend | Explicit provenance, controllable collection frame, reproducible analysis | Not platform-wide; labor-intensive; panel bias and missing data | Best immediate learning-project route |
| Paid social-listening vendor | Vendor-defined platform mentions/metrics | Typically paid service and potentially separate data rights | Could offer broader managed coverage | Cost, opaque coverage/method, vendor terms and export constraints | Excluded by the zero-additional-spend project constraint |

## Practical staged design

### Stage 0 — Keep today's output honest

- Keep `extract` user-triggered and capped. Preserve source URL, query, capture timestamp, visible date if actually present, candidate/readable counts and per-candidate status.
- Display `N of M candidate pages readable`; never imply these are all posts or representative posts.
- Call keyword matches **visible-text matches**. Do not call them “trending,” “popular,” “viral,” or “growing.”
- Keep the existing metadata-only opt-in capture behavior. No captions, account credentials, tokens or raw page content in snapshots.
- Use the manual paste/CSV route only for public or otherwise authorized text the user may share. Record that pasted text is user supplied and unverified.

### Stage 1 — Build a measurable local panel

1. Define one narrow cohort: e.g., a consenting set of creator-owned/public accounts or a researcher-curated set of URLs that can be lawfully observed. Do not describe it as Instagram-wide.
2. Fix the cadence and query, locale, timezone, inclusion rules and observation window. A weekly cadence with at least four complete weeks is a reasonable prototype, but the interval should reflect the use case.
3. Store metadata locally: `query`, `source_kind`, URL, observed date, capture UTC, readable status, and explicit source/permission note. Keep raw caption storage opt-in and retention-bounded; current snapshot feature should remain metadata-only.
4. Deduplicate by canonical post URL. Record missing/unavailable posts; never turn unavailable into zero. Track the denominator each run.
5. Report raw counts per cohort and interval, sample size, readable fraction, and difference from the preceding equal-length interval. Suppress a trend label when the cohort or denominator changed materially.
6. Review a sample manually for ambiguous matches, language variants, and duplicated/reposted content. Keep keyword matching distinct from semantic topic classification.

**Exit criterion:** With a fixed, documented cohort and at least two comparable windows, the app may say “within this sample, observed count changed from X to Y.” Reserve “trend” for longer repeated measurement with stable coverage and a clear baseline.

### Proposed finance protocol — manual, consented panel (not an existing capability)

This is a repeatable, zero-cost study procedure using contributor-supplied records and the existing CSV/manual-analysis paths. It is **proposed, not implemented or automated**. It does not use Google result counts as post counts, automate Instagram browsing, or claim to measure Instagram as a whole.

1. **Fix the cohort and source.** Recruit a named panel of 10 finance creators who agree to submit links to their own public posts for this exercise. Keep the same panel list for every run. Each creator submits up to five of their newest eligible posts from the completed week, with a visible caption excerpt or permission for a human reviewer to inspect that public page. Record a stable panel ID, source URL, and whether the text was creator-supplied or human-observed. Do not collect posts from non-panel accounts or use a logged-in/browser automation workaround.
2. **Fix the query and window.** Run once weekly at 12:00 UTC on Monday for the immediately preceding Monday 00:00:00 through Sunday 23:59:59 UTC. Keep two separate exact queries: the hashtag token `#finance` and the phrase `personal finance`. Match case-insensitively; for `#finance`, require hashtag token boundaries, and for the phrase, require the exact contiguous words. Never merge `finance`, `#finance`, and `personal finance` into one series. Record query text, match rule, window bounds, and run timestamp.
3. **Record denominators and missingness.** For each run report: 10 cohort members; contributors who submitted at least one item; submitted URLs; unique URLs with a usable publication date inside the window; readable/inspectable in-window posts; unreadable or unverified in-window posts; undated submissions; out-of-window posts; and exact-query matches among readable in-window posts. Show both `matches / readable in-window posts` and `readable in-window posts / unique dated in-window URLs`; a missing page, excerpt, date, or submission is unknown, never a non-match or zero. Undated items remain in the missingness report but are excluded from the weekly signal. Count participating creators as well as posts so one prolific creator cannot silently dominate the result.
4. **Deduplicate and validate.** Canonicalize URLs by lowercasing the host and removing query strings, fragments, and trailing slashes; deduplicate on that canonical URL while retaining the first submitted URL for provenance. Do not deduplicate different URLs merely because their captions look alike. Check that each item belongs to the fixed cohort, has a usable publication date inside the stated week, and has text evidence for the exact match. Preserve a status for excluded, duplicate, unreadable, undated, out-of-window, and reviewed items.
5. **Compare cautiously.** Compare the current complete week only with the immediately preceding complete week, using the same 10 creators, the same set of participating creators, per-creator five-post cap, query, match rule, and date rules. Pre-register a provisional display gate of at least 8 of the same participating creators and 30 readable, dated in-window posts in each week; this is an operational guardrail, not a statistically validated threshold. If either week misses the gate or creator/readability coverage differs, show counts and denominators but no directional label. If both pass, say only “in this consented finance panel, exact-match posts were X/Y readable in-window posts this week versus A/B last week”; do not call it an Instagram trend, population share, growth rate, or forecast.
6. **Minimize retained data.** Store the weekly aggregate, query/window, coverage counts, match counts, missingness counts, and source URLs only as needed for verification. Do not persist full captions or sensitive information by default. Delete contributor-level records on request and document the retention period before collection.

This protocol would establish a consistent, auditable **panel sample** if completed repeatedly. Even a stable panel is self-selected and capped at five posts per creator; it cannot establish Instagram-wide volume, audience interest, or trend prevalence. Until the protocol is run on real consented inputs, the current five-result Google sample remains only a bounded discovery demonstration.

### Stage 2 — Verify official API feasibility before coding

Use a read-only feasibility spike, no paid infrastructure and no credentials placed in chat or source control:

- Check current Meta docs for login configuration, professional account requirement, scopes, App Review/Advanced Access/business verification, public-content eligibility, hashtag query cap, returned fields, pagination, and retention/deletion rules.
- Confirm the exact intended use case with Meta's review requirements. If access requires an approval the project does not have, stop there; keep the manual/CSV pathway.
- Only if approval and non-billable access are verified, define a minimal scope, obtain authorization through Meta's official consent flow, store secrets outside the repository, and test one hashtag with the smallest request. Do not silently substitute third-party scraping APIs or paid services.
- Document the API's actual coverage limits and observed missingness before exposing metrics. Treat counts of fetched records as sample counts unless Meta explicitly provides a population count with a documented definition.

**Gate:** No implementation until a project owner confirms app eligibility and intended access is authorized. This is a product/access gate, not a request for budget; the project constraint remains zero additional spending.

### Stage 3 — Corroborate and communicate

- For search demand, export Google Trends data using fixed geography and period, preserve its 0–100 relative scale, and cite it as search interest.
- Compare it with the local Instagram panel/API series only as a separate signal. If the indicators disagree, report the disagreement rather than forcing a single score.
- Consider a “candidate topic to investigate” label, not an automatic content recommendation claiming demand.

## Failure modes to guard against

| Failure mode | Why it misleads | Control |
|---|---|---|
| Calling a higher Google result count a trend | Index inclusion and ranking change independently of Instagram posting | Report returned/readable candidates only; no growth rate from search-result counts |
| Comparing `finance` with `#finance` as equivalent | Different query and matching semantics; hashtag may be tokenized differently | Preserve exact query and analyze variants separately |
| Treating visible dates as complete dates | Some pages expose no date; visible date may be relative or localized | Store only explicitly parsed/observed values with timezone/precision; otherwise null |
| Comparing two capped 5-result samples | Top results/availability can rotate; no fixed denominator | Label as sample URL overlap/change; avoid population trend wording |
| Equating search interest with social usage | Google searches and Instagram posts are different behaviors | Keep separate series with source labels |
| Treating a login wall as a scrape challenge | Encourages unauthorized access and creates false confidence | Stop; use an authorized API or manual records the user may share |
| Assuming API approval | The app endpoint, scope and use case may be gated | Feasibility/readiness gate before any integration |
| Treating a single viral post as a topic trend | Anecdotal selection and engagement skew | Use repeated windows, cohort definitions and distribution-aware summaries |

## Decisions and open evidence gaps

1. **Do not currently ship a “trend” score from `extract` or URL snapshot comparison.** Those paths have no complete denominator or observed velocity.
2. **Keep the current feature as bounded discovery.** The latest live check read all five candidates in each of two samples, but earlier checks had unavailable candidates and this tiny, changing Google-indexed sample is not a stable extraction-rate estimate.
3. **Prefer local CSV/panel analysis for the next zero-cost iteration.** It is controllable and auditable but must be labeled as a selected sample.
4. **Treat the Meta API as a conditional future route.** The overview and endpoint docs conflict on Unified Login versus Facebook Login; current endpoint scopes, media bounds, and quota are documented above, but Unified endpoint authentication, feature-review handling, and whether generic finance research qualifies remain unverified. No access is configured.
5. **No vendor services or paid APIs.** That would violate the project's zero-additional-spending constraint.

## Source index

- Meta, [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform/overview) — product/API capabilities and account model; exact requirements should be checked against current endpoint-specific docs.
- Meta, [Unified Login permissions](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/permissions) and [Unified Login setup](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started) — new-login access and token setup; note the mismatch with endpoint-specific auth requirements.
- Meta, [IG Hashtag Search](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag-search), [IG Hashtag](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag), [top media](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag/top-media), [recent media](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag/recent-media), and [recently searched hashtags](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/recently_searched_hashtags) — endpoint permissions, media fields/eligibility, and rolling query limit.
- Meta, [Instagram Public Content Access feature](https://developers.facebook.com/documentation/development/features-reference#reference-INSTAGRAM_PUBLIC_CONTENT_ACCESS) and [Instagram API App Review](https://developers.facebook.com/documentation/instagram-platform/app-review) — allowed-use examples and review/access information.
- Meta, [How We Combat Scraping](https://about.fb.com/news/2021/04/how-we-combat-scraping/) — Meta's distinction between authorized crawling and unauthorized automated collection.
- Meta, [New Tools to Support Independent Research](https://about.fb.com/news/2023/11/new-tools-to-support-independent-research/) — Content Library/API description and researcher context; announcement updates are on that page.
- Instagram, [Terms of Use](https://help.instagram.com/581066165581870) — current controlling terms; page returned HTTP 429 in this research pass, so the relevant clause was not independently reread here.
- Google Search Central, [How Google Search works](https://developers.google.com/search/docs/fundamentals/how-search-works) — crawling/indexing is selective and algorithmic; some discovered pages are not crawled or inaccessible.
- Google Trends Help, [FAQ about Google Trends data](https://support.google.com/trends/answer/4365533?hl=en) — sampling, relative normalization, low-volume zeros, noise and interpretation cautions.
- SocialRum Lab, [Instagram workflow](instagram-workflow.md) — current app behavior, access boundaries, one historical post success, direct hashtag login wall, and metadata-only capture design.

**Readiness:** Ready to keep the bounded extraction workflow and improve its sample reporting. **Not ready to claim Instagram keyword/hashtag trends or to implement the Meta API** until the coverage, access approval and usable time-series signal are demonstrated.
