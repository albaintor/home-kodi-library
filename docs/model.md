# Data Model and Persistence

## Catalog and Connectors

- **Source**: Kodi MariaDB database or CLZ XML collection, configuration, last import, and any error.
- **Work**: movie, TV show, or episode, identified by UUID; consolidated metadata for search and display.
- **Edition/record**: a stable entry from a source. Several CLZ discs or Kodi files/versions can belong to the same work.
- **Identifiers**: normalized IMDb/TMDB/TVDB identifiers, associated with the media type. Barcodes and CLZ identifiers remain in the edition metadata.
- **Playback target**: Kodi HTTP instance explicitly linked to a Kodi source.
- **Job**: asynchronous import/synchronization, status, and counters.

Connectors produce the same internal `Record` structure. A future connector converts its export/API into this format and reuses the snapshot and matching logic. The model reserves space for source-specific metadata.

Matching requires a shared IMDb, TMDB, or TVDB identifier, an identical media type, and no contradiction with known identifiers. A similar title never merges a remake with its original. Ambiguous cases remain separate and are counted in `matching_conflicts`. There is not yet a manual conflict-resolution screen.

A Kodi synchronization processes a complete snapshot of entries still present in the MyVideos database. Kodi editions that are no longer present are deleted from SQLite; a work with no remaining source is removed from the catalog and its lists. If a work also has CLZ editions (matched by a shared identifier), it remains in the catalog: its metadata is rebuilt from CLZ, missing Kodi files are no longer offered for playback, and personal ratings/lists are preserved. The job result distinguishes removed editions, deleted works, and works retained through CLZ. The report shows the actual MyVideos database queried, removed titles (up to 20, plus the total count), and explicitly indicates when no deletion was detected in that snapshot. File deletions are observable only after the Kodi library and database have actually been cleaned up; the MariaDB connection does not check file existence on network shares.

Kodi metadata takes precedence, with CLZ filling in missing fields. Formats, genres, and collections are combined. The date added favors Kodi, then CLZ. SQLite FTS5 search is accent-insensitive and queries the local catalog even when MariaDB or Kodi is offline.

## Watch Status

The API distinguishes `watched`, `unwatched`, `in_progress`, and `unknown`.

- Kodi: play count and resume point; for TV shows, watched/total episode counts.
- CLZ: `<seenhistory><seendate><date><year>…</year><month>…</month><day>…</day></date></seendate></seenhistory>`. At least one `seendate` means watched; each entry counts as one viewing. Valid dates are stored in `metadata.seen_dates`, and the latest one in `last_played`.
- An explicitly empty `seenhistory` means unwatched. A missing element means unknown, since the export may omit this field. An invalid date does not erase the fact that a viewing was recorded.

A work is **watched if at least one edition/source reports it as watched**, even if the Kodi file is unwatched. Otherwise, it is in progress if Kodi reports a resume point or a partially watched TV show; otherwise, it is unwatched if a source explicitly reports that state; otherwise, it is unknown. Per-edition statuses remain available to distinguish a "watched disc" from an "unwatched Kodi file". No status is written to Kodi or CLZ.

Play counts use the maximum known value across sources/editions rather than their sum: the same viewing may be recorded more than once. The last-played date is the most recent known. CLZ dates without a time use `YYYY-MM-DD`; Kodi timestamps retain their time without inventing a timezone, and CLZ timestamps are converted to UTC.

## SQLite and Backups

The `/data` volume contains `catalogue.db` (SQLite WAL) and the `artwork/` cache. A single server process is used, with indexed access, FTS5, atomic transactions, and a lock timeout. The schema version is tracked using `PRAGMA user_version` (v2, automatic migration from v1); a newer schema prevents startup.

Source/target passwords are stored in the local database in plaintext at rest, protected by volume permissions and redacted from responses. The directory is private and the SQLite file uses mode 0600. Protect and back up this volume as you would a configuration file containing secrets.

For a reliable backup, stop the container and copy **the entire `data` directory**, or use the SQLite backup API. Do not copy only `catalogue.db` while a WAL write is in progress. Also keep `.env` in a protected location. Interrupted jobs are marked as failed on the next startup; the last committed snapshot remains available.

The poster cache retains files for 30 days and evicts the oldest files beyond 512 MiB during writes. Image files are limited to 8 MiB; only recognized raster images are accepted. Private image URLs, redirects, and URLs containing credentials are rejected to prevent imported metadata from triggering arbitrary requests to the local network.

## Artwork Stored on the NAS

If Kodi or CLZ contains `smb://nas/media/posters/film.jpg`, mount the corresponding directory:

```yaml
# compose.yaml, under volumes
- /volume1/media/posters:/artwork:ro
```

```dotenv
# .env: JSON object, prefix ending with a slash
ARTWORK_MOUNTS='{"smb://nas/media/posters/":"/artwork/"}'
```

A local prefix such as `/home/user/covers/` or `file:///covers/` can be mapped the same way. The suffix is resolved beneath the mount; `../` path traversal and symlinks pointing outside it are rejected. Kodi `image://` URLs are decoded by the connector. A private HTTP image also requires an explicit mapping to a mounted file, or an accessible public URL.

## Limitations of This Version

The connector relies on `movie_view`, `tvshow_view`, `episode_view`, `art`, and `uniqueid`, with optional columns depending on the version. Queries are read-only, and missing optional views do not prevent movies from being imported. Validation against the actual MyVideos version is required before claiming support for all metadata, particularly alternative versions.

Schemas supporting movie versions do not all use the same numeric type: `VERSION=0` in [Kodi 21](https://github.com/xbmc/xbmc/blob/21.0-Omega/xbmc/video/VideoManagerTypes.h), `VERSION=1` in the [current Kodi source](https://github.com/xbmc/xbmc/blob/master/xbmc/video/VideoManagerTypes.h). The connector detects the type from default movie files, retains alternative versions, and excludes extras. Older schemas without this column remain supported.

`actor` and `actor_link` provide cast members, roles, and cast order. `streamdetails` provides video resolution, codecs, HDR type, stereoscopy, and audio tracks and languages per file. Optional HDR fields remain unknown when absent. The TrueHD codec alone does not prove Atmos; CLZ can supplement the work with explicitly labeled Dolby Atmos tracks. The initial CLZ export provided does not contain cast data. No additional database or SQLite migration is required for this metadata, which is stored in editions.

CLZ TV shows are collection editions; any episode lists they contain are not imported as Kodi episodes. Playback targets Kodi files, not automatic playback of a physical disc. Wake-on-LAN and separate user profiles are not yet implemented.

## Installation-Specific Data

`personal` stores personal ratings and watched statuses by work UUID; `null` means reverting to imported data. Consolidation into `works` applies these choices during each synchronization. They are not written to Kodi/CLZ sources.

`lists` and `list_items` store selections, manual positions, and revisions. Display sorting is independent of saved positions. Deleting a work with no remaining source removes its associations and increments the revisions of affected lists.

`enrichment` stores TMDB credits and links, with timestamps, separately from editions. Exact IMDb/TMDB identifiers avoid approximate title-based searches. Imported data takes precedence for roles; TMDB supplements portraits and identifiers by name among the credits for the same media. People and roles are included in FTS search. Links and portrait URLs exposed in the detail view are constructed by the server. Images use the shared proxy/cache and its protections against private URLs.

## Server Versions, Schema, and Clients

`/api/v1/status` exposes `version` and `data_model_version`.
iOS discovery (`/api/v1/discovery`) exposes `server_version` and the same
`data_model_version`.

The server identifier contains a `+model.N` suffix derived from
`DATA_MODEL_VERSION` in `home_media_catalog/compatibility.py`. **Any change
to the SQLite schema or a REST data model must increment this
constant.** The server version suffix then changes automatically.
The release version in `pyproject.toml` and `home_media_catalog/__init__.py`
is updated for each release.

SQLite `PRAGMA user_version` and the mobile contract bounds
`MIN_MOBILE_CONTRACT` / `MAX_MOBILE_CONTRACT` are independent: an
additive change must not make older iOS clients incompatible.
