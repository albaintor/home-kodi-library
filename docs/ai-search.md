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

**Privacy**: A search transmits the user's prompt, context filters, and the
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

V3: for conversational refinement, send the **complete previously returned**
`plan` as `previous_plan` with the new prompt:
`{"prompt":"plutôt des années 90","previous_plan":{...}}`.
The server asks the model to preserve unaffected constraints. The client displays
the conversation, and "New search" clears the prior plan. Conversations are
client-held; no history database is needed.

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

## iOS entry point

The native magnifying-glass Search tab **continues to use ordinary title/FTS
search**. At the top of that tab, the prominent **Search with AI** button
(opens a separate conversational sheet) is the dedicated natural-language
entry point. It remains accessible even when iOS 26 replaces its tab bar with
the native search field; the old icon-only toolbar shortcut was unreliable.

To verify server credentials on either client, save the key/models first,
then choose **Test OpenAI connection** in the AI settings. Each of the two
configured models is validated independently. A valid API key alone is not
enough if a selected model is unavailable or the project has exhausted quota.
