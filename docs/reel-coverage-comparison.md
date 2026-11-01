# Reel coverage comparison worksheet

**Status (2026-11-01):** worksheet and evidence boundaries prepared; no vendor account, trial, Meta app, API permission, or paid service was enrolled. Product behavior has not been compared by running equivalent queries in each product.

This document supports [the 25-to-50 reel coverage plan](../../../prp-plans/socialrum/2026-11-01-reel-coverage-25-to-50-plan.md). It separates (1) claims visible in public product material, (2) prior local observations, (3) the proposed future test, and (4) unknowns. A product page is evidence that a vendor makes a claim; it is not independent proof of capability, data provenance, representativeness, or performance.

## What the available evidence says

| Evidence class | Evidence | What it supports | What it does not support |
|---|---|---|---|
| Local observed run | The plan records finance as 10/10 readable captions and education as 10/11 candidates readable in one prior live session. | Those two query runs achieved those observed counts under the then-current workflow. | A stable success rate, a 25-record guarantee, generalization across niches/time, or complete/balanced discovery. Small, non-random samples. |
| Proposed lab contract | Default target 25 readable, validated records; configurable maximum target 50; at most 100 unique candidate attempts and bounded time. | The intended UX and hard bounds, if implemented. | Existing implemented behavior or guaranteed coverage. A target is a stopping goal; shortfall is valid output. |
| SocialRum public site | The vendor describes trend discovery, competitor analysis, scripts and video editing; its page also markets searching reels/creators, outlier-style filters, and tracking in one feed. [SocialRum](https://socialrum.com/) | Public vendor-stated positioning and advertised feature areas, checked 2026-11-01. | That a given control works, has a specific source/backend, covers the same candidate universe, supports export, or is available in the current beta. The earlier dynamic capture was not consistency/readiness-qualified. |
| Octupie public site | The vendor describes niche mapping, a daily feed of account-relative outlier reels, reel breakdowns and personalized scripts. The page advertises free access and paid plans/trials with stated prices. [Octupie](https://www.octupie.com/) | Public vendor-stated positioning and the pricing shown on the page on 2026-11-01. | That its daily feed contains all relevant reels, that its signals match our query-based sample, or that the listed price/terms will remain current. No account was created and no trial was started. |
| Published sampling example | A 2025 study reviewed the first 100 #sunscreen reels presented to each of two accounts and reported no statistically significant difference in the analyzed categories between those accounts. [JMIR Dermatology study](https://pmc.ncbi.nlm.nih.gov/articles/PMC12588582/) | A narrow, dated example of sampling reels through two accounts and comparing a particular coding outcome. | General unbiasedness of Instagram discovery; transfer to other hashtags, keywords, topics, ranking dimensions, dates, or Google-indexed results. The authors identify limits including one hashtag, one platform and one time point. |
| Search-index behavior | Google says crawling, indexing and serving are not guaranteed. [Google Search: How Search Works](https://developers.google.com/search/docs/fundamentals/how-search-works) | A Google-derived candidate list is an index-mediated sample whose availability may be incomplete and change over time. | Instagram-wide, real-time, ranked-by-Instagram, or exhaustive results. Query date filters are not verified post publication dates. |
| Platform policy | Meta states that automated collection from its products without prior permission violates its terms. [Meta: How We Combat Scraping](https://about.fb.com/news/2021/04/how-we-combat-scraping/) | A material policy boundary for any automation that directly accesses Instagram. | Whether a particular API use is eligible or whether Google-indexed discovery is authorized in a particular deployment. Check applicable terms and permissions; stop at access boundaries. |

The proposed local method discovers candidates from Google-indexed pages and reads visible page content via the existing local workflow. It is not an Instagram search API and should be described as **Google-indexed discovery with visible-page reads**. Counts must distinguish search candidates, unique attempts, nonempty visible captions, validated source associations, and any keyword-match judgment.

## Fair comparison protocol (future work)

This is a worksheet, not a completed benchmark. Fill a row only when the workflow was actually exercised and its evidence captured. Keep each system's native workflow visible; do not force an ostensibly identical input into a product that does not accept it. Record `N/A` with a reason instead of inventing a comparable mode.

### Query set

Use paired phrase/hashtag prompts, without assuming the phrase and hashtag are semantically identical. Record spelling, locale, date/time and any interface filters verbatim.

| Pair | Topic | Keyword phrase | Hashtag | Run IDs |
|---|---|---|---|---|
| F1 | Finance | `personal finance India` | `#personalfinanceindia` | pending |
| F2 | Finance | `index fund investing India` | `#indexfunds` | pending |
| F3 | Finance | `credit card rewards India` | `#creditcards` | pending |
| E1 | Education | `study techniques India` | `#studytips` | pending |
| E2 | Education | `spoken English practice` | `#learnenglish` | pending |
| E3 | Education | `math learning` | `#maths` | pending |

These are starting prompts, not validated taxonomies. Before comparing, note ambiguity, language/region and whether a product supports a direct query, an account/niche onboarding flow, or only a curated feed. Use separate bounded sessions if required. Do not raise access volume to meet a target.

### Per-run record

Duplicate this table for every system × query × run. Preserve timestamps and the exact visible input. For vendor runs, capture only data the product exposes through ordinary authorized use; do not infer its internal collection method.

| Field | Entry |
|---|---|
| Run ID / system / product version | pending |
| Date, start/end time, timezone | pending |
| Exact query or onboarding inputs | pending |
| Discovery route shown to user | pending; quote UI label or mark unknown |
| Filters / sort / date scope | pending; distinguish visible setting from inferred behavior |
| Candidate count shown | pending / not exposed |
| Unique reel URLs observed | pending / not exposed |
| Attempted opens / failures | pending / not exposed |
| Nonempty visible captions | pending / not exposed |
| Source association verified | pending / not exposed; record verification rule |
| Query relevance reviewed | pending; reviewer and simple rubric |
| Freshness evidence | pending; observed post date vs product/index date vs unknown |
| Deduplication evidence | pending; method and duplicate count or unknown |
| Export/copy available in tested tier | pending / not tested |
| Stop reason / partial-result behavior | pending / not exposed |
| Price and access terms displayed that day | link + timestamp / not tested |
| Evidence artifact | local path or public source; redact account data |
| Limits and anomalies | pending |

### Denominators and parity

Report at least these denominators separately; write `not exposed` when the product does not reveal a count.

| Measure | Definition |
|---|---|
| Candidate yield | unique reel candidates returned or visibly surfaced; denominator is query/run |
| Read success | unique items with nonempty visible caption ÷ unique items actually opened/attempted |
| Association success | items with demonstrated caption-to-reel association ÷ items with a nonempty visible caption |
| Relevant share | items judged relevant under a prewritten rubric ÷ items independently reviewed |
| Target completion | validated readable items ÷ requested readable target; capped at 1 for display, while raw count remains visible |

Parity means the same prompt, observation window, locale assumptions and documented stopping point—not equal internal datasets. For account/niche products, explain the onboarding inputs and show those results in their native mode; do not label them a direct query match. Blindly review relevance if feasible. Save a dated, privacy-appropriate aggregate rather than committing raw captions, usernames, screenshots or credentials.

Do not compare one tool's “outlier” feed (a ranking/selection product) as if it were a complete query result set. Do not infer a winner from two earlier lab runs. If a system's item count, source, date, or denominator is hidden, mark it unknown and limit the conclusion accordingly.

## API feasibility and permitted-source options

### Meta API: feasibility is unresolved for this use case

Meta's developer documentation is the authority for endpoint and permission behavior. On 2026-11-01, the directly referenced developer endpoint page could not be fetched through the available web research route (HTTP 429), so this worksheet does **not** treat hashtag-search eligibility, scopes, app review, returned media fields, or limits as verified for this project. An official Meta Postman collection describes the Instagram API with Facebook Login as serving Professional (Business/Creator) accounts and includes hashtagged media discovery, but it also documents account/linkage requirements and says consumer accounts are unsupported. This is useful orientation, not confirmation that our app may use the endpoint for arbitrary research queries. [Meta Instagram API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api)

| Question to verify against current official docs / approved app | Status | Implementation consequence |
|---|---|---|
| Does an official endpoint support hashtag discovery for this app and intended research purpose? | Unknown; endpoint reference was rate-limited in this research pass. | Do not implement or describe API access as available. |
| Does API access support arbitrary keyword phrases, or only hashtag lookup? | Unknown for current app/use case. | Do not imply keyword search can be translated to API calls. |
| Which Instagram account type, linked assets, permissions, access level and app review are required? | General professional-account and linked-Page requirements appear in the Meta Postman collection; exact current permission/review gates for this app are unverified. | No app creation, permission request, account conversion or production-token work is in scope. |
| Which reel/media fields, dates, captions, pagination and metrics are returned and permitted to retain? | Unknown for this project; must be checked field by field in current official endpoint docs and terms. | No assumptions about caption completeness, ranking, dates, analytics or retention. |
| Is the proposed collection purpose allowed by Meta policy/terms? | Not established. Meta says automated collection requires prior permission. | API presence alone is not permission to use results for this application; obtain policy/eligibility confirmation before any API-backed design. |
| Does access have a charge or require paid resources? | No paid route has been evaluated or approved. | No paid enrollment or service proposed. If official access entails cost or account commitments, exclude it unless the project owner changes the zero-spend constraint. |

The plan's current design remains the existing local browser workflow plus controlled-response tests. This is not a claim that browser automation is authorized merely because content is public; Meta's published position on automated access applies, and the workflow must respect applicable permission boundaries. A challenge, login wall, consent wall, or other access restriction is a stop condition.

### Other source options

- **User-provided approved export:** potentially feasible if the user already has a lawful, permissioned export and its provenance, consent, fields, date range and reuse terms are documented. None is currently available in this task. Do not assume export availability.
- **Manual public research:** useful for a small qualitative sample where a person opens pages normally, records only necessary observations and follows platform terms. It is slower and does not establish platform-wide coverage.
- **Google-indexed candidates:** available in the existing workflow but index-mediated, incomplete by design and not a substitute for Instagram live search. Report index result count and time; do not call it real-time trend coverage.
- **Vendor products:** compare only what is visible in authorized access. Marketing pages do not reveal the backend's data source or whether it has platform permission. No trial was started as part of this worksheet.

## Decision boundary

Until the open questions above are answered with current primary documentation and project-specific eligibility evidence:

1. Keep the official Meta API as an investigated option, not a dependency or capability claim.
2. Do not create Meta app credentials, request scopes, create/convert accounts, start a trial, or provision paid resources as part of this comparison.
3. Keep the lab result label explicit: Google-indexed candidate discovery; visible-page read; source-association status; observed time; and shortfall/stop reason.
4. Present vendor capabilities as dated vendor claims unless directly tested; report unavailable evidence as unknown.

## Source notes

- Plan baseline and local observations: [25-to-50 coverage plan](../../../prp-plans/socialrum/2026-11-01-reel-coverage-25-to-50-plan.md), dated 2026-11-01.
- Vendor statements: [SocialRum homepage](https://socialrum.com/) and [Octupie homepage](https://www.octupie.com/), accessed 2026-11-01. These are vendor-authored pages; Octupie's displayed prices are time-sensitive. Do not treat page copy or imagery as independent validation.
- API orientation only: [Meta's Instagram API Postman collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api). Direct Meta developer endpoint fetch returned 429 during this pass; recheck official docs before any API decision.
- Index limitations: [Google Search: How Search Works](https://developers.google.com/search/docs/fundamentals/how-search-works).
- Access policy: [Meta: How We Combat Scraping](https://about.fb.com/news/2021/04/how-we-combat-scraping/).
- Narrow sampling study: [JMIR Dermatology, 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12588582/).
