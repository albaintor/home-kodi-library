# Third-party licences and acknowledgements

This page identifies the third-party components used by the server/web and iOS/iPadOS applications, their usage, licences and copyright/source references.

[← Project overview](README.md) · [Features](docs/features.md) · [Native iOS project](https://github.com/albaintor/home-media-catalog-ios)

## Components and licences

### Server and web interface

| Component | Actual project use | Licence and source |
| --- | --- | --- |
| [FFmpeg / FFprobe](https://ffmpeg.org/) | `Dockerfile` installs the Debian `ffmpeg` package; executable used for network-video previews, thumbnails, probing, browser remux and transcoding | LGPL for LGPL-configured builds; GPL variants also exist. [Licences and source](https://ffmpeg.org/legal.html) |
| [libnfs](https://github.com/sahlberg/libnfs) | `Dockerfile` installs Debian `libnfs14` for NFS access | LGPL-2.1-or-later for the core library. [Copyright and licence](https://github.com/sahlberg/libnfs/blob/master/COPYING) |
| [FastAPI](https://github.com/fastapi/fastapi), [Uvicorn](https://github.com/encode/uvicorn), [HTTPX](https://github.com/encode/httpx) | Python API, ASGI server and HTTP clients | [FastAPI (MIT)](https://github.com/fastapi/fastapi/blob/master/LICENSE) · [Uvicorn licence](https://github.com/encode/uvicorn) · [HTTPX (BSD-3-Clause)](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| [PyMySQL](https://github.com/PyMySQL/PyMySQL), [smbprotocol](https://github.com/jborean93/smbprotocol), [Paramiko](https://github.com/paramiko/paramiko) | Optional Kodi/MariaDB integration, SMB and SFTP connections | [PyMySQL (MIT)](https://github.com/PyMySQL/PyMySQL/blob/main/LICENSE) · [smbprotocol (MIT)](https://github.com/jborean93/smbprotocol/blob/master/LICENSE) · [Paramiko (LGPL-2.1)](https://github.com/paramiko/paramiko/blob/main/LICENSE) |
| [Pillow](https://github.com/python-pillow/Pillow), [cryptography](https://github.com/pyca/cryptography), [USearch](https://github.com/unum-cloud/usearch) | Artwork manipulation, security/secret handling and optional HNSW vector search | [Pillow (MIT-CMU)](https://github.com/python-pillow/Pillow/blob/main/LICENSE) · [cryptography (Apache-2.0 or BSD-3-Clause)](https://github.com/pyca/cryptography/blob/main/LICENSE) · [USearch (Apache-2.0)](https://github.com/unum-cloud/usearch/blob/main/LICENSE) |
| Other Python packages | Refer to [`requirements.txt`](requirements.txt), [`pyproject.toml`](pyproject.toml) and the actual container environment | Additional packages have their respective upstream licences |
| Browser/platform functionality | HTML5 video, browser APIs and runtime media stack | Browser and operating-system implementations are not software redistributed as part of the Python dependencies |

The Docker base image, Debian packages and additional Python dependencies retain their respective copyright and licence notices.

### Native iOS/iPadOS client

The iOS app lives in [albaintor/home-media-catalog-ios](https://github.com/albaintor/home-media-catalog-ios). Its current `project.yml` (development line) declares a **`prismcore-dynamic` branch** of the [albaintor/ffmpeg-apple](https://github.com/albaintor/ffmpeg-apple/tree/prismcore-dynamic) fork, **not** an immutable direct SwiftPM pin to Wenzlik/PrismCore 3.8.0. The fork retains PrismCore's upstream licence and derives from that version. The fork retains PrismCore's upstream copyright and licence terms.

| Component | What the project uses it for | Licence, attribution and source |
| --- | --- | --- |
| **PrismCore** (source-derived from 3.8.0) | Demux, remux, media-stream integration and AVPlayer assistance | Copyright © 2026 Václav Zmrhal; **LGPL-2.1-or-later with an Application Store Exception applying to PrismCore only**. [Fork licence/exception](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/Legal/PrismCore/LICENSE) · [NOTICE](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/Legal/PrismCore/NOTICE.md) · [upstream](https://github.com/Wenzlik/PrismCore) |
| **MPVKit** (adapted Wenzlik fork) | Dependency wrapper for FFmpeg, libmpv, libbluray and related libraries | LGPL-3.0 for the adapted non-GPL package. [Adapted package](https://github.com/albaintor/ffmpeg-apple/tree/prismcore-dynamic/adapters/MPVKit) · [upstream fork](https://github.com/Wenzlik/MPVKit) · [licence](https://github.com/Wenzlik/MPVKit/blob/main/LICENSE) |
| **FFmpeg libraries** | Codec/demux/remux and software video/audio processing; seven `Libav*` / `Libsw*` iOS binaries currently point to [albaintor/ffmpeg-apple build-v8.1.3-r2](https://github.com/albaintor/ffmpeg-apple/releases/tag/build-v8.1.3-r2) | The adapted package references **dynamic** FFmpeg frameworks, and the upstream PrismCore notice describes an LGPL-3.0-or-later version-3 FFmpeg configuration. FFmpeg also has GPL-configured variants. [FFmpeg legal](https://ffmpeg.org/legal.html) |
| **libmpv / mpv** | Native binary included in MPVKit's dependency set; not an assertion of an exposed mpv player UI | GPL-2.0-or-later by default; LGPL-2.1-or-later for the LGPL build option. [mpv copyright and licensing](https://github.com/mpv-player/mpv/blob/master/Copyright) |
| **libbluray** | MPVKit binary dependency; experimental Blu-ray ISO title navigation and BDMV-related development | LGPL; the adapted MPVKit package references **Libbluray 1.4.0**. [VideoLAN project and licence information](https://www.videolan.org/developers/libbluray.html). This library does **not** by itself decrypt commercial AACS/BD+ discs |
| **AMSMB2 + libsmb2** | iOS SMB2/SMB3 share browsing, streaming and file operations; exact SwiftPM package **AMSMB2 4.0.3** | AMSMB2 source includes MIT-licensed code, but its authors designate the **distributed package LGPL-2.1** because of its linked `libsmb2` dependency. [Project and linking notice](https://github.com/amosavian/AMSMB2).  |
| **NFSKit** | iOS NFS share access; upstream revision `ec63d055b9ee08d609d3c6cb32f42c07edc7bdf7` | MIT; © 2021 alexiscn. [Source](https://github.com/alexiscn/NFSKit) · [text bundled in iOS app](https://github.com/albaintor/home-media-catalog-ios/blob/main/HomeMediaCatalog/Legal/NFSKit-MIT.txt) |
| **libnfs** | NFSKit's native static XCFramework; server also separately includes Debian `libnfs14` | LGPL-2.1-or-later for the library. [Source and COPYING](https://github.com/sahlberg/libnfs) |
| **MySQLNIO** | Standalone read-only MariaDB/Kodi catalogue integration | MIT. [Licence](https://github.com/vapor/mysql-nio/blob/main/LICENSE) |
| **SwiftNIO, NIOSSL and SwiftLog** | Swift networking, TLS and logging utilities | Apache-2.0; [SwiftNIO](https://github.com/apple/swift-nio), [SwiftNIO SSL](https://github.com/apple/swift-nio-ssl) and [SwiftLog](https://github.com/apple/swift-log) |
| **TrueHDObjects + Rust crates** | Transitive PrismCore binary; TrueHD/Atmos object processing | Apache-2.0 core and separate nested crate notices. [Upstream notices](https://github.com/Wenzlik/PrismCore/blob/3.8.0/Rust/TrueHDObjects/THIRD-PARTY-NOTICES.md) · [licence directory](https://github.com/Wenzlik/PrismCore/tree/3.8.0/Rust/TrueHDObjects/licenses) |
| **libdovi / dovi_tool** | Transitive Dolby Vision RPU parsing in media stack | MIT; [upstream](https://github.com/quietvoid/dovi_tool) |
| **Additional MPVKit binaries** | May include libass, FreeType, FriBidi, HarfBuzz, OpenSSL, GnuTLS, nettle, GMP, libplacebo, dav1d, MoltenVK, shaderc and others | Each included library retains its own copyright and licence terms. [MPVKit component manifest](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/adapters/MPVKit/Package.swift) |
| **Apple AVFoundation, VideoToolbox, Vision, Metal** | iOS native playback, interpolation and graphics | Apple SDK frameworks governed by Apple's agreements, not FFmpeg or GNU licences |

The iOS media stack uses an adapted MPVKit dependency and PrismCore with their respective upstream licences.

SVPflow, SVPlayer, RIFE, libdvdnav and libdvdcss are **not** included as third-party dependencies. MEMC interpolation and DVD IFO parsing are implemented separately.

## Cloud services, data and media rights

- **OneDrive** is the currently integrated cloud provider. Microsoft terms and API permissions are separate from open-source licences. 
- **OpenAI, Anthropic Claude and Google Gemini** are supported as optional external API providers on the server. API access is subject to each provider's terms, usage policies, privacy notices and billing. Their services are **not redistributed software libraries**; no affiliation or endorsement is implied.
- **TMDB and other metadata/artwork services** have API, attribution, branding and image-content terms. User-owned videos, Blu-ray/DVD material, subtitles and cover art have separate copyright and access restrictions. A software licence does not convey rights to protected media.
- **libbluray** provides Blu-ray parsing and navigation; its software licence does not convey rights to Blu-ray movie content.

## Licence terms

Each library is used under its own licence. MIT, BSD and Apache licences grant rights to use, copy, modify and redistribute software under their notice and attribution conditions. GNU LGPL licences set out the corresponding rights and conditions for library source, modification, replacement and distribution in a combined work. **PrismCore's Application Store Exception applies only to PrismCore**, not to FFmpeg, MPVKit or other libraries.

## Official references

[FFmpeg licensing](https://ffmpeg.org/legal.html) ·
[GNU LGPL-2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) ·
[GNU LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html) ·
[PrismCore licence with App Store Exception](https://github.com/albaintor/ffmpeg-apple/blob/prismcore-dynamic/Legal/PrismCore/LICENSE) ·
[AMSMB2 licence](https://github.com/amosavian/AMSMB2) ·
[libnfs COPYING](https://github.com/sahlberg/libnfs).
