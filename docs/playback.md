# Playback — configuring source locations

The `/api/v1/playback/locations` API stores source descriptors
(`id`, `name`, `kind`, `target`) in the server's SQLite database.
Authenticated clients can read them; creating, updating and deleting
locations requires administrator access. The iOS client caches descriptors
per server URL so the Playback tab can display them without a connection.
Secrets are excluded from this synchronization.

## Supported playback sources

- Videos already downloaded or imported on iOS: played through
  AVPlayer/PrismCore without the server.
- Videos selected through the iOS Files app and its providers (iCloud Drive,
  OneDrive, Google Drive, Dropbox, and SMB when configured in Files): can be
  imported into the app container for offline playback. Provider accounts
  are configured separately in Files.
- HTTP and HTTPS media URLs: streamed on iOS when reachable.
- FTP and NFS source descriptors: stored and synchronized independently
  of their browsing/streaming adapters. Direct protocol access and
  server-side gateways require appropriate configured adapters or mounts.
  SMB paths do not automatically become visible to the server.
- NAS credentials and cloud OAuth authorizations are managed by
  separate secure connector mechanisms, **not** by this descriptors API.
  Never put credentials in `target`.

An early version of the locations API supported only descriptor storage.
The full network explorer and cloud OAuth implementations are separate
components; they must be configured and supported for browsing to work.
Importing a file creates a local copy and consumes iOS device storage.

All file gateways must validate paths, enforce an allowlist of source
locations, protect HTTP Range requests and use short-lived playback
tickets that do not expose shared-folder credentials.

## Cleaning the Kodi media library

The admin-only `POST /api/v1/sources/{source_id}/clean` operation starts
`VideoLibrary.Clean` through JSON-RPC WebSocket on the Kodi instance
associated with the source. Optional `player_id` selects a specific
instance. Cleaning is separate from `/scan` and `/sync`.

The server waits for `VideoLibrary.OnCleanStarted` followed by
`VideoLibrary.OnCleanFinished` before resynchronizing the catalogue.
If the WebSocket connection or completion confirmation fails, the
local catalogue remains unchanged; a SQL fallback is not misreported
as a successful cleanup. For multi-edition Kodi libraries, synchronization
uses MariaDB when configured to avoid losing version information
not exposed by JSON-RPC.

**Warning:** verify that every SMB/NFS mount and disk is accessible
before cleaning. Otherwise Kodi may remove valid entries from its
library. The operation does **not** delete physical video files.
Progress and errors: `GET /api/v1/jobs/{job_id}`.
