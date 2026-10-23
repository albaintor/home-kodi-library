# AI catalogue search — structured filters, semantic retrieval and conversational refinement

Home Kodi Library works without an AI provider. Normal SQLite FTS5/title search
is never disabled, and configuration and index files are not part of JSON backups.

## Server setup

An administrator opens **Settings → Recherche intelligente · IA** (web) or
**Settings → AI search** (iOS), enters:

- The OpenAI text model (default `gpt-4o-mini`), for extracting validated filters
  and carrying out follow-up refinements.
- The OpenAI embeddings model (default `text-embedding-3-small`), for semantic
  similarity between prompts and media descriptions.
- An OpenAI API key, only if AI search is wanted.

Both clients use the same server settings. The key is saved in
`DATA_DIR/ai-settings.json` (permissions 0600) and never returned to clients.
The vector index is stored in `DATA_DIR/ai-vectors.db`. These files can contain
sensitive data (including descriptions); back them up securely, separately from
the catalogue JSON export. Remote access should use HTTPS.

**Privacy**: A search transmits the user's prompt, context filters, and (when refining)
metadata for the current candidate titles, together with the
catalogue's available genre/format values to OpenAI. Vector indexing explicitly
sends **titles, genres, film/series summaries and each imported episode synopsis**
to the configured OpenAI embeddings API, in batches. Vectors are stored locally
on the server. Do not enable this feature if transferring those descriptions
to a third party is unacceptable. No Kodi database password or API token is
sent as part of those requests.

## Choosing the AI models

The web and iOS settings use guided selectors, with a custom-model option to
support future OpenAI releases and preserve existing configurations.

- **Language model**: translates the user's request into structured filters,
  explains them and preserves previous constraints in multi-turn searches.
  `gpt-6-luna` is the recommended economical option; `gpt-6.1-sol` is an
  advanced, more expensive choice. Existing `gpt-4o-mini` configurations
  remain selectable for compatibility.
- **Embedding model**: encodes the titles and synopses of individual films,
  series and episodes into vectors for similarity matching. Prefer
  `text-embedding-3-small` (USD 0.02 per million input tokens at the time of
  writing). `text-embedding-3-large` can improve similarity quality but costs
  USD 0.13 per million input tokens. These are separate API calls from the
  language model.

Changing **only** the language model needs no reindexing. Changing the embedding
model uses a different vector space, so the semantic index must be built for
that model. If an existing configuration uses a custom model ID, clients
preserve it rather than silently substituting a preset.

## Indexing

Use the administrator-only **Indexer / actualiser** button in web settings,
or **Index or refresh summaries** in iOS. The server builds an incremental
semantic index, including individual episode plots and their parent series titles.
It reuses unchanged vectors and deletes stale records when a full pass completes.
A semantic search can also start index refresh automatically if the corpus is
missing or has changed since the previous scan. After importing or synchronizing
Kodi, triggering a new semantic search checks for changed episodes.

The index progress appears as `indexed / total`; the first indexing pass can
be longer and consumes embedding API tokens. Changing the embeddings model
causes an index to be built for that model. The UI exposes errors such as
a missing provider or an invalid embedding model. When the provider is absent,
ordinary searches are unaffected.

## Incremental indexing, token consumption, and spending limits

After every successful Kodi synchronization or XML collection import that
changes catalogue records, the server schedules a **debounced asynchronous**
vector refresh. Metadata enrichment and source deletion also request a refresh.
Kodi/import publication does not wait for embeddings; several close-together
changes are coalesced. The existing `ai-vectors.db` hashes prevent charges for
unchanged synopses. Watched-state or rating changes are not part of indexed
texts, so they do not trigger unnecessary embedding calls.

An administrator can set **Monthly OpenAI spending cap (USD)** in the Web or
iOS AI settings (default **$5.00/month**; **$0** blocks all paid calls).
The cap is enforced before outbound OpenAI requests — including automatic
indexing, natural-language interpretation, embedding queries, relevance
reranking, and connection tests. Requests reserve a conservative estimated
cost before transmission; when OpenAI returns usage counters, the ledger
reconciles actual input/output/cached tokens and estimated USD cost.
For network failures where OpenAI might still have billed a request, the
reservation remains an estimate instead of being silently discarded.
Concurrent calls reserve against a single SQLite month ledger.

The dashboard, via admin-only `GET /api/v1/ai/usage`, displays the current
UTC calendar month, cost estimate, budget remaining, and request/token totals
for indexing, searching, and connection tests. The `monthly_budget_usd`
field is saved with the other settings in `PUT /api/v1/ai/settings` and
is preserved when older clients omit it. A cap is an **application-side
safety control, not a substitute for the provider's billing limits**:
pricing is based on public standard API rates, and the estimate excludes
taxes, different service tiers and external account activity. Custom
model identifiers without known prices are rejected before billable calls
rather than bypassing the cap. The ledger is held locally in
`DATA_DIR/ai-usage.db` (mode 0600) without storing access keys or prompts.

## API (authenticated)

- `GET /api/v1/ai/status`: configured state, model IDs, index status.
- `GET /api/v1/ai/settings`: same fields, administrator only.
- `PUT /api/v1/ai/settings`: administrator only. Payload example
  `{"model":"gpt-4o-mini","embedding_model":"text-embedding-3-small","api_key":"…"}`.
  Omit the key to preserve it or use `clear_api_key:true` to disable AI.
- `POST /api/v1/ai/connection/test`: administrator-only live test of the
  saved OpenAI language model (Responses API structured JSON output) and the
  embeddings model. Two small billable requests are made. Returns separate
  `language` and `embedding` status objects with safe error codes such as
  `invalid_key`, `model_unavailable`, or `quota_or_rate_limit`. Never returns
  the token or raw upstream error body.
- `POST /api/v1/ai/index/refresh`: administrator-only incremental full scan.
- `POST /api/v1/ai/search`: `{"prompt":"unwatched sci-fi involving a time loop"}`.
  The response includes catalogue media, structured `plan`, ranked mode,
  matched episodes (`episode_matches`), index progress, and optional note.

## Conversational refinement and result lists

On iOS, follow-up prompts reuse the current search session. The client sends
the previously returned `plan` as `previous_plan` and, when the server
advertises `ai_refine_results`, up to 60 currently displayed media IDs as
`previous_ids`. The server reads candidate metadata from its own database;
the client does not supply trusted titles or arbitrary search results.

The model distinguishes three operations:

| Operation | Example | Result |
| --- | --- | --- |
| `filter` | "Retire Valérian de la liste" | Keeps the other displayed titles and removes matching items. |
| `expand` | "Ajoutes-en d'autres" | Keeps the current selection and searches for additional titles using the previous search plan; duplicate IDs are excluded. |
| `search` | "Cherche plutôt des films de Miyazaki" | Runs a fresh search, with conversational criteria interpreted by the server. |

Example follow-up request:

```json
{
  "prompt": "Retire Valérian de la liste",
  "previous_plan": {
    "kind": "movie",
    "watch_status": null,
    "genre": null,
    "format": null,
    "year_min": null,
    "year_max": null,
    "rating_min": null,
    "keywords": "",
    "semantic_query": "science-fiction",
    "assistant_message": ""
  },
  "previous_ids": ["existing-media-id-1", "existing-media-id-2"]
}
```

The response carries `items`, `plan`, `mode` (`refine`, `expand`,
`hybrid` or `filters`), `assistant_message` and relevant search metadata.
The IDs in the example are placeholders, not valid catalogue IDs.

The iOS **Undo last refinement** action restores the previous result snapshot
and conversation state without sending another request. **New search** clears
the current results, history and previous criteria. A failed network request
leaves the prior results intact.

The interface displays at most 60 results. Expanding a full list cannot
display more than 60 simultaneously; it is not unlimited pagination.
**Add all AI results to a list** adds the currently displayed IDs only, to
an existing or new list, using the server's `bulk_list_add` capability.
This action requires administrator access. When a server does not advertise
`ai_refine_results`, the iOS client omits `previous_ids` and falls back to
the earlier plan-only refinement, rather than sending an unsupported field.
The discovery endpoint advertises optional capabilities, independently of the
mobile data-contract version.

## Ranking and episode results

A structured plan is validated server-side; the model never supplies executable
SQL. Semantic cosine similarity and SQLite FTS5 title/plot matches are blended,
then exact year, genre, format, rating and watch constraints are applied through
the existing catalogue database. Only titles already imported into the server
can be returned. A matching episode is shown as a result under its parent show,
with season/episode number and a synopsis excerpt; the original episode ID is
retained for navigation.

The semantic score is approximate and reflects the quality of imported
synopses. A lexical title/name request uses FTS5; plot/theme queries require an
indexed embedding. Initial results show at most 60 works; the index is refreshed
incrementally. The server limits AI requests to 10/minute per client IP and
3 concurrent upstream searches.

A missing or broken provider is surfaced to the user: the server never silently
pretends that a semantic result was computed. FTS5 and the standard catalogue
remain available without an AI API key.

## iOS entry point (Media Catalog 1.2 build 7)

Ordinary title and metadata search uses Apple's native SwiftUI `searchable`
modifier **on the existing catalogue grid/list**, not an independent Search
tab. Its query updates the catalogue's standard filters; the same results
are displayed in the main catalogue. On iOS 26 the system chooses its
standard bottom-aligned search presentation on iPhone.

The **sparkles** button in the catalogue's **top toolbar** opens a dedicated
AI search page with a standard SwiftUI `NavigationLink` (no popup).
That page owns its native multiline prompt and Search action. Prompts are
interpreted, matched with metadata filters and semantic similarity, and
optionally refined in subsequent turns. The AI session is owned by the
connected catalogue tab and is preserved when navigating to media details
and back. Matched episodes open by their exact ID.

A media detail now first retrieves the local server resource directly with
`GET /api/v1/media/{mediaID}`; the former automatic TMDB enrichment could
delay the first display for seconds. Users can still explicitly refresh
TMDB metadata from the detail view.

To verify credentials on Web or iOS, save both model choices and the key,
then select **Test OpenAI connection**. Each model is validated
independently; a valid key alone does not guarantee quota or model access.

## Search precision and verified relevance

A vector embedding is only a retrieval heuristic, not proof of a creator's
style. Semantic searches now apply a stricter minimum similarity and ask the
configured language model to validate shortlisted film/episode descriptions
against the entire original user request. This relevance validation transmits
the candidate titles, genres and synopsis excerpts to OpenAI at search time,
in addition to the indexing-time embeddings requests. This helps reject false matches
(such as a generic fantasy comedy for a Miyazaki-inspired animation request).
The response exposes `relevance_reasons` with grounded brief explanations for
retained media IDs. Weak candidates are omitted; no result is preferable to a
misleading match. The additional relevance pass makes a second language-model
API call and has corresponding cost. Structured catalogue constraints such
as watched status remain enforced by SQLite.
