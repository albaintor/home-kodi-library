# Third-party licences, component usage and distribution obligations

_Last reviewed: 30 October 2026. Covers the public Docker/web project and the companion native iOS/iPadOS development project. This is a documented dependency review, **not** a complete software bill of materials (SBOM), legal advice or a certification of distribution compliance._

[← Project overview](README.md) · [Features](docs/features.md) · [Native iOS project](https://github.com/albaintor/home-media-catalog-ios)

## Where third-party software is used

### Server and web interface

| Component | Actual project use | Licence / information to verify |
| --- | --- | --- |
| [FFmpeg / FFprobe](https://ffmpeg.org/) | `Dockerfile` installs the Debian `ffmpeg` package; executable used for network-video previews, thumbnails, probing, browser remux and transcoding | Commonly LGPL unless compiled with GPL modules or other licence-changing options. **The precise licence is build-specific.** [FFmpeg legal/compliance](https://ffmpeg.org/legal.html) |
| [libnfs](https://github.com/sahlberg/libnfs) | `Dockerfile` installs Debian `libnfs14` for NFS access | Core library LGPL-2.1-or-later; confirm the package's source and copyright data |
| [FastAPI](https://github.com/fastapi/fastapi), [Uvicorn](https://github.com/encode/uvicorn), [HTTPX](https://github.com/encode/httpx) | Python API, ASGI server and HTTP clients | Upstream licences and installed package metadata apply |
| [PyMySQL](https://github.com/PyMySQL/PyMySQL), [smbprotocol](https://github.com/jborean93/smbprotocol), [Paramiko](https://github.com/paramiko/paramiko) | Optional Kodi/MariaDB integration, SMB and SFTP connections | Check exact packages, transitives and licence files |
| [Pillow](https://github.com/python-pillow/Pillow), [cryptography](https://github.com/pyca/cryptography), [USearch](https://github.com/unum-cloud/usearch) | Artwork manipulation, security/secret handling and optional HNSW vector search | Check exact versions, binary wheels, transitive dependencies and upstream notices |
| Other Python packages | Refer to [`requirements.txt`](requirements.txt), [`pyproject.toml`](pyproject.toml) and the actual container environment | Generate an SBOM from the **built image**, not just direct dependencies |
| Browser/platform functionality | HTML5 video, browser APIs and runtime media stack | Browser and operating-system implementations are not software redistributed as part of the Python dependencies |

The Docker image uses `python:3.12-slim-trixie`; that base image and its Debian packages have their own notices, copyright files and redistribution requirements. The server's static JavaScript/CSS and any bundled assets must also be inspected for vendor licences.

### Native iOS/iPadOS client

The iOS app lives in [albaintor/home-media-catalog-ios](https://github.com/albaintor/home-media-catalog-ios). Its current `project.yml` (development line) declares a **`prismcore-dynamic` branch** of the [albaintor/ffmpeg-apple](https://github.com/albaintor/ffmpeg-apple/tree/prismcore-dynamic) fork, **not** an immutable direct SwiftPM pin to Wenzlik/PrismCore 3.8.0. The fork retains PrismCore's upstream licence and derives from that version. Exact dependency commits must be taken from the build's resolved package graph.

| Component | What the project uses it for | Licence, attribution and source |
| --- | --- | --- |
| **PrismCore** (source-derived from 3.8.0) | Demux, remux, media-stream integration and AVPlayer assistance | Copyright © 2026 Václav Zmrhal; **LGPL-2.1-or-later with an Application Store Exception applying to PrismCore only**. [Fork licence/exception](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/Legal/PrismCore/LICENSE) · [NOTICE](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/Legal/PrismCore/NOTICE.md) · [upstream](https://github.com/Wenzlik/PrismCore) |
| **MPVKit** (adapted Wenzlik fork) | Dependency wrapper for FFmpeg, libmpv, libbluray and related libraries | LGPL-designated non-GPL product; audit exact binary targets. [Adapted package](https://github.com/albaintor/ffmpeg-apple/tree/prismcore-dynamic/adapters/MPVKit) · [upstream fork](https://github.com/Wenzlik/MPVKit) · [licence](https://github.com/Wenzlik/MPVKit/blob/main/LICENSE) |
| **FFmpeg libraries** | Codec/demux/remux and software video/audio processing; seven `Libav*` / `Libsw*` iOS binaries currently point to [albaintor/ffmpeg-apple build-v8.1.3-r2](https://github.com/albaintor/ffmpeg-apple/releases/tag/build-v8.1.3-r2) | The adapted package labels these binaries as **dynamic**; actual FFmpeg licence depends on their build flags and linked dependencies (the upstream PrismCore NOTICE describes `--enable-version3` and LGPL-3.0-or-later). Verify the actual build's flags and source. [FFmpeg legal](https://ffmpeg.org/legal.html) |
| **libmpv / mpv** | Native binary included in MPVKit's dependency set; not an assertion of an exposed mpv player UI | [mpv copyright/licensing](https://github.com/mpv-player/mpv/blob/master/Copyright); verify the actual binary/build configuration |
| **libbluray** | MPVKit binary dependency; experimental Blu-ray ISO title navigation and BDMV-related development | LGPL; the adapted MPVKit package references **Libbluray 1.4.0**. [VideoLAN project and licence information](https://www.videolan.org/developers/libbluray.html). This library does **not** by itself decrypt commercial AACS/BD+ discs |
| **AMSMB2 + libsmb2** | iOS SMB2/SMB3 share browsing, streaming and file operations; exact SwiftPM package **AMSMB2 4.0.3** | AMSMB2 source includes MIT-licensed code, but its authors designate the **distributed package LGPL-2.1** because of its linked `libsmb2` dependency. [Project and linking notice](https://github.com/amosavian/AMSMB2). **Review actual linkage/relinkability** before App Store distribution |
| **NFSKit** | iOS NFS share access; upstream revision `ec63d055b9ee08d609d3c6cb32f42c07edc7bdf7` | MIT; © 2021 alexiscn. [Source](https://github.com/alexiscn/NFSKit) · [text bundled in iOS app](https://github.com/albaintor/home-media-catalog-ios/blob/main/HomeMediaCatalog/Legal/NFSKit-MIT.txt) |
| **libnfs** | NFSKit's native static XCFramework; server also separately includes Debian `libnfs14` | LGPL-2.1-or-later for the library. [Source and COPYING](https://github.com/sahlberg/libnfs) · check whether iOS's link arrangement satisfies LGPL |
| **MySQLNIO** | Standalone read-only MariaDB/Kodi catalogue integration | MIT. [Licence](https://github.com/vapor/mysql-nio/blob/main/LICENSE) |
| **SwiftNIO, NIOSSL and SwiftLog** | Swift networking, TLS and logging utilities | Apache-2.0; [SwiftNIO](https://github.com/apple/swift-nio), [SwiftNIO SSL](https://github.com/apple/swift-nio-ssl) and [SwiftLog](https://github.com/apple/swift-log) |
| **TrueHDObjects + Rust crates** | Transitive PrismCore binary; TrueHD/Atmos object processing | Apache-2.0 core and separate nested crate notices. [Upstream notices](https://github.com/Wenzlik/PrismCore/blob/3.8.0/Rust/TrueHDObjects/THIRD-PARTY-NOTICES.md) · [licence directory](https://github.com/Wenzlik/PrismCore/tree/3.8.0/Rust/TrueHDObjects/licenses) |
| **libdovi / dovi_tool** | Transitive Dolby Vision RPU parsing in media stack | MIT; [upstream](https://github.com/quietvoid/dovi_tool) |
| **Additional MPVKit binaries** | May include libass, FreeType, FriBidi, HarfBuzz, OpenSSL, GnuTLS, nettle, GMP, libplacebo, dav1d, MoltenVK, shaderc and others | **Mixed licences**. Inventory each actual included XCFramework, notice and link mode from the [MPVKit package manifest](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/adapters/MPVKit/Package.swift); no blanket licence classification is asserted |
| **Apple AVFoundation, VideoToolbox, Vision, Metal** | iOS native playback, interpolation and graphics | Apple SDK frameworks governed by Apple's agreements, not FFmpeg or GNU licences |

The iOS project also contains older/local adapted MPVKit files; **the resolved build graph and embedded frameworks**, rather than directory names, establish which binaries are shipped. The PrismCore/MPVKit dynamic-media migration remains experimental: some published FFmpeg XCFramework module headers and iOS build/archive checks still require validation.

**No shipped SVPflow, SVPlayer or RIFE dependency is asserted.** The app's Metal/MEMC algorithm is independently implemented; comparisons with other players are not licence grants, endorsements or claims of feature parity. DVD IFO parsing in the current development line is custom application code; no `libdvdnav`/`libdvdcss` integration is asserted.

## Cloud services, data and media rights

- **OneDrive** is the currently integrated cloud provider. Microsoft terms and API permissions are separate from open-source licences. Do not describe Google Drive, Dropbox or other cloud providers as shipped connectors merely because their names appear in development discussions.
- **OpenAI, Anthropic Claude and Google Gemini** are supported as optional external API providers on the server. API access is subject to each provider's terms, usage policies, privacy notices and billing. Their services are **not redistributed software libraries**; no affiliation or endorsement is implied.
- **TMDB and other metadata/artwork services** have API, attribution, branding and image-content terms. User-owned videos, Blu-ray/DVD material, subtitles and cover art have separate copyright and access restrictions. A software licence does not convey rights to protected media.
- Blu-ray parsing/navigation **does not grant DRM circumvention rights**. The current libbluray integration is not an AACS/BD+ decryption implementation; encrypted discs may not work.

## Release compliance checklist — not yet signed off

1. **Freeze the build graph.** Record the exact `Package.resolved`, PrismCore fork commit, MPVKit package and **every XCFramework checksum**, iOS archive slices, Docker image digest, Debian packages and installed Python distributions. Generate an SBOM with full transitive dependencies and licence texts.
2. **Audit binaries independently.** Confirm the shipped FFmpeg `avcodec_configuration()` / `ffmpeg -buildconf`, enabled GPL/nonfree/third-party libraries, and that the adapted MPVKit dependency graph does not unexpectedly link GPL-variant products. Static/dynamic declarations are insufficient without inspecting the final `.ipa` / Mach-O binaries.
3. **Meet LGPL combined-work conditions.** For the actual FFmpeg, libmpv/MPVKit, libbluray, libsmb2/AMSMB2 and libnfs combinations, check rights to modify/reverse-engineer for debugging, provision of matching source, build instructions, relink/replacement mechanism and any applicable installation information. Dynamic FFmpeg reduces one particular static-linking problem; it **does not** automatically satisfy the LGPL for other statically linked components.
4. **Apply exceptions narrowly.** PrismCore's Application Store Exception is **for PrismCore only**. It does not waive FFmpeg, MPVKit, AMSMB2/libsmb2, libnfs, libbluray or any transitive library obligations. A commercial/non-consumable StoreKit purchase does not change those obligations.
5. **Publish the required materials.** Make the accurate copyright notices, complete applicable licence texts, matching source revisions, local modifications, reproducible build/relink materials and any required source offer accessible to recipients. This Markdown page and external hyperlinks **alone do not necessarily satisfy** redistribution conditions.
6. **Expose notices inside the app.** Provide a discoverable **Settings → About → Open-source licences** route, verify the packaged notices correspond to the actual IPA and include any mandatory reproduction of copyright notices and permission texts.
7. **Audit the Docker distribution separately.** Include applicable Debian/Python/base-image notices, source information and FFmpeg configurations; ensure the published container is evaluated independently of iOS.
8. **Verify Apple distribution and external-rights terms.** Review App Store signing, device replacement/debug rights, media patents, service/API terms, metadata artwork rights and end-user terms before release.

**Compliance status:** **not certified for App Store or Docker redistribution** by this document. A release-specific review remains required, particularly for LGPL-linked iOS components and the experimental dynamic FFmpeg build.

## Official references

[FFmpeg legal and distribution checklist](https://ffmpeg.org/legal.html) ·
[GNU LGPL-2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) ·
[GNU LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html) ·
[PrismCore licence with App Store Exception](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/Legal/PrismCore/LICENSE) ·
[AMSMB2 licence and linking notice](https://github.com/amosavian/AMSMB2) ·
[libnfs COPYING](https://github.com/sahlberg/libnfs) ·
[Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/).
