# AI search (opt-in)

The server supports prompt-to-filter search using OpenAI. No API key is required to
run the application; standard SQLite FTS5 search continues unchanged.

An administrator configures **Settings → AI** (web or iOS), entering an OpenAI
model ID and API key. The key is stored only in `DATA_DIR/ai-settings.json`,
with mode 0600. It is never returned by the API and is deliberately excluded
from catalogue backups. Back up this file separately if needed. Protect the
NAS backup and use HTTPS for remote clients.

* `GET /api/v1/ai/status` (authenticated): provider, model, configured.
* `GET /api/v1/ai/settings` (admin): same metadata, and has_api_key.
* `PUT /api/v1/ai/settings` (admin): `{"model":"gpt-4o-mini","api_key":"…"}`;
  omit/null/empty API key to retain, `clear_api_key: true` to deactivate.
* `POST /api/v1/ai/search` (authenticated): `{"prompt":"unseen science fiction after 2015"}`.
  The response includes the first 60 matching media and the parsed `plan`.

Requests send the prompt and available genre/format labels to OpenAI, **not
the entire catalogue**. The model returns structured filter parameters, which
the server validates and executes with parameterized SQLite queries. The model
cannot generate SQL, change the catalogue, or invent matching titles.

**Scope of V1:** Exact structured filters (type, watch state, genre, format,
year range, minimum editorial rating) and optional lexical FTS. Subjective
plot-level similarity is not implemented yet; that requires local embeddings
(V2). Provider failures return an error while standard search still works.
