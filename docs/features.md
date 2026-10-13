# Features and usage

[← Overview](../README.md) · [Install the server](installation.md)

## Full feature list

- Title-only search (localized and original titles) by default; optional advanced search across synopses, genres, collections, cast, and other metadata. Searches are accent-insensitive and support word-prefix matching.
- Combinable filters: movies/TV shows/episodes, Kodi/CLZ, genre, video/audio/disc formats (multiple selections combined with AND), collection, watched/unwatched/in progress/unknown, and additions from the past 30 days. The API also supports release year, a specific source, and parent TV series. Video resolution **4K** is distinct from physical **4K UHD / Blu-ray 4K** discs. Filter and sort preferences are saved in the browser.
- Sort by title, date added, year, rating, or last watched, in ascending or descending order. Infinite scrolling loads batches of 60 items, with a fallback button and recovery after errors.
- Poster grid and list views; detailed media pages with ratings, synopses, physical editions, Kodi files, resume positions, and playback history.
- Responsive details with Kodi cast members and roles, resolution and 3D badges, Dolby Vision/HDR10/HDR10+, Dolby/DTS audio, and physical media formats. Per-edition technical details remain available through the API.
- **Read-only** synchronization from Kodi MariaDB, covering movies, TV shows, episodes, IMDb/TMDB/TVDB identifiers, and alternative versions when exposed by `movie_view`.
- CLZ Movies XML import: editions, Blu-ray/4K/3D/DVD formats, barcodes, box sets, languages, subtitles, and `seenhistory`.
- Kodi/CLZ matching using reliable external identifiers and media type, with a conservative fallback to unambiguous title **and** year matches. Editions remain attached to their respective sources.
- Multiple Kodi HTTP/JSON-RPC players associated with a MariaDB source. Play or resume on an **already running** Kodi instance without relying on Home Assistant entities.
- MariaDB and Kodi connection tests before adding a source: networking, authentication, MyVideos database, read permissions, and Kodi HTTP control. These tests are also available through the API.
- Persistent local SQLite database and artwork cache: **no second MariaDB server**.
- Administrator token, optional read-only token, browser sessions with CSRF protection, and a separate Ingress secret.

The web interface uses exactly the same API as other clients. No essential server-side business logic lives exclusively in the frontend.

## Native iOS/iPadOS application

The native SwiftUI client uses the same versioned API and catalog as the web interface. After the server is installed, use local Bonjour discovery where available, or enter the server URL. Generate a short-lived code in **Settings → iOS Pairing** in the web interface. A native client uses its own token for remote access, not the Home Assistant Ingress secret.

## Cast, portraits, and trailers

Media details distinguish cast, directing, writing, production, composing, cinematography, and other credits. Kodi provides roles, directors/writers, and portraits already available in its database. CLZ-exported credits/portraits and YouTube links are imported when present. The original sample export has empty cast sections: images visible in CLZ Cloud are not included in that XML.

To enrich movies and TV shows automatically with TMDB, place the **API Read Access Token** from [your TMDB account](https://www.themoviedb.org/settings/api) in `.env`, then rebuild or restart the server as described in the [installation guide](installation.md#updating-an-existing-installation). An **API v3 key** is also accepted in the same setting. Your TMDB account password is not valid here.

```dotenv
TMDB_API_TOKEN=your_TMDB_read_access_token
```

Under **Settings → Metadata and portraits · TMDB**, the UI reports whether TMDB is configured and offers **Test TMDB connection**. Invalid credentials, network problems, and rate limits produce explicit errors. Each media page also indicates when TMDB is unavailable. Enabling TMDB does not require reimporting the catalog: reopening a media page is sufficient when it already has an IMDb/TMDB identifier.

An administrator opening a media page triggers enrichment using the title's IMDb/TMDB identifier. The process fetches cast, crew, portraits, and YouTube trailers without fuzzy title matching. **Names and portraits** link to TMDB/IMDb profiles; otherwise an explicitly labeled **IMDb search** link is provided. The YouTube button appears at the top of the media page: it opens the only trailer directly, lets you choose when several are available, or searches YouTube if no trailer was supplied.

Metadata persists in SQLite across restarts and synchronization. It is reused for 30 days and refreshed at the next opening afterward. **Refresh TMDB metadata** forces an update and invalidates affected cached images; if a request fails, previously saved information is retained. Portraits use the same authenticated image proxy, 30-day/512 MiB cache, and security checks as posters. API clients can request enrichment independently of the web frontend. TMDB is optional: imported or cached content remains accessible without network access or a TMDB token.

The browser remembers search terms, filters, sort direction, view mode, and other preferences. Facets that no longer exist in the catalog are ignored on reload. Background catalog scrolling is locked while media details or a list are open and its position is restored on close; nested dialogs have independent scrolling.

## Ratings, watch status, and lists

At the top of each media page, the star control lets you set **your personal rating out of 10** using hover, click, or keyboard input; a star or half-point slider saves immediately. The **Watched / Unwatched** badge toggles and saves the status on click without a separate Save button. These personal values are shared by users of the installation, persist in SQLite, and take precedence over imports. Clearing a rating or choosing **From sources** restores the imported value. Public IMDb/Kodi ratings, play counts, and source files remain imported information: personal edits do not write back to MariaDB or CLZ. No additional database server is required.

In the catalog, the **Custom lists** section offers **＋ Create a list** and **Open my lists**. **My lists** also remains accessible in the header. Creating a list opens the name form directly; the same form appears on first opening when no list exists. Administrator access is required to create or edit lists; read-only access displays an explanation in the dialog.

**My lists** supports creating, renaming, and deleting lists; searching for **movies or TV shows** to add via **＋ Add media**; removing items; and moving them with ↑/↓ in **Manual order, ascending** mode. A media page also has a **＋ Add to list** button, including the option to create a list from that dialog; existing associated list names are clickable. The last opened list and its sorting preference are saved in the browser. Sorting by title, year, date added, public rating, or personal rating changes the view only and does not modify the saved manual order. Lists use pagination and revision numbers to prevent concurrent changes from silently overwriting a reordering operation. They are separate from imported CLZ collections and box sets.

Work UUIDs preserve list associations during normal synchronization. If a media item disappears from every source, it is removed from its lists; deleting a list does not delete any media. Upgrading automatically migrates SQLite from schema version 1 to version 2. Back up the `data` volume before downgrading: older releases cannot read schema version 2.

## Kodi file paths and playback

Full file paths are visible on media pages, with a **Copy path** action (on HTTP, a copy dialog is shown instead of clipboard access). **Play on Kodi** contacts the selected player from the backend and lets you select the file/version. Explicit selection opens that exact path, even for a default version. Kodi uses its own access permissions to the media files.

If no player is associated with the source, the media page offers **Configure a Kodi player**. The Kodi-logo button opens playback options: player, file/version, and resume position. HTTP API and file-read permission tests are available for saved players under **Settings**. Playback launch errors remain visible in the playback dialog. A successful command means Kodi accepted it; access to the file still depends on Kodi's media storage permissions.

Web browsers do not provide native SMB/NFS playback. Opening these protocol links depends on a local protocol handler and its share permissions. `file://` links are also restricted from web pages. There is no universal local-playback link; users can open the path in a compatible player with access to media files. This release does **not** stream or transcode video files over HTTP. [Browser protocol handlers](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/registerProtocolHandler) · [Kodi Player.Open](https://kodi.wiki/view/JSON-RPC_API/v13.11).

For Docker deployment, permissions, updates and troubleshooting, see [server installation](installation.md).
