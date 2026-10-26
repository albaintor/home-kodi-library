# Third-party notices — server/web and companion iOS app

_Last reviewed: 26 October 2026. This is a component guide, not a legal opinion or a full dependency SBOM._

## Server / Docker image

- **FFmpeg** — the `Dockerfile` installs the distribution package `ffmpeg` via apt. This executable is invoked for remote-video thumbnails, previews and browser-compatible transcoding. FFmpeg is generally LGPL, **but its actual distribution licence depends on the binary's configuration and included optional GPL libraries**. Inspect `ffmpeg -version`, `ffmpeg -buildconf` and Debian package copyright/source metadata for each built image. See https://ffmpeg.org/legal.html.
- **Python packages** — application dependencies include FastAPI, httpx, Pillow, cryptography, smbprotocol, Paramiko and other packages from `pyproject.toml` / `requirements.txt`. Their respective upstream copyrights/licences apply. Produce a complete inventory from the actual build; this list is not exhaustive.
- **Browser assets and metadata integrations** — honour copyrights, notices and API terms of artwork providers (e.g. TMDB), Kodi and cloud services separately. Source code licensing does not grant distribution rights in movies, promotional stills or third-party logos.

## Native iOS client

The iOS app lives in [albaintor/home-media-catalog-ios](https://github.com/albaintor/home-media-catalog-ios) (repository access may be restricted). Its `project.yml` pins **PrismCore 3.8.0** from https://github.com/Wenzlik/PrismCore, which includes an LGPL media dependency chain via the Wenzlik MPVKit fork.

| Dependency | Copyright / terms | Source and notices |
| --- | --- | --- |
| **PrismCore 3.8.0** | Copyright © 2026 Václav Zmrhal, LGPL-2.1-or-later **with Application Store Exception for PrismCore's own code** | [Full licence](https://github.com/Wenzlik/PrismCore/blob/3.8.0/LICENSE) · [NOTICE](https://github.com/Wenzlik/PrismCore/blob/3.8.0/NOTICE.md) |
| **FFmpeg via MPVKit** | The transitive `--enable-version3` FFmpeg builds are **LGPL-3.0-or-later** in PrismCore's documentation. Other binaries/variants can have different licences | [FFmpeg Legal](https://ffmpeg.org/legal.html) · [MPVKit fork](https://github.com/Wenzlik/MPVKit) |
| **MPVKit** | LGPL product plus separate GPL-labelled variant; audit the actual linked product | [Licence](https://github.com/Wenzlik/MPVKit/blob/main/LICENSE) |
| **TrueHDObjects and nested crates** | Apache 2.0 and nested MIT/Apache/other notices; bundled transitively | [TrueHDObjects notices](https://github.com/Wenzlik/PrismCore/blob/3.8.0/Rust/TrueHDObjects/THIRD-PARTY-NOTICES.md) |
| **libdovi** | MIT | [Repository](https://github.com/quietvoid/dovi_tool) |

SVP/SVPlayer/RIFE are **not** claimed as shipped dependencies of the application.

## Release compliance gate

1. Create a complete inventory of the **exact** binaries in each iOS IPA and Docker image, including transitive native frameworks and compiler flags.
2. Verify no forbidden/incompatible `--enable-gpl`, `--enable-nonfree` or other licence changes were introduced.
3. Distribute the copyright attributions and **full applicable licence texts** with the product; include a reachable About/Open-source licences screen in the app.
4. Provide matching source/build scripts, patches and license-required materials for FFmpeg, MPVKit and every other LGPL component. See FFmpeg's [licence compliance checklist](https://ffmpeg.org/legal.html).
5. For the iOS app, assess the **statically linked LGPLv3** components' relinking, modification/debugging and applicable installation-information requirements (LGPLv3 §4). **PrismCore's special App Store exception covers only PrismCore, not FFmpeg or MPVKit.** An App Store distribution method that cannot meet those obligations requires a different legally compatible backend/permission before release.
6. Check terms for media, codec/patent rights, OpenAI/other API providers, artwork and branding.
7. Repeat the review for every released build, including monetised builds. The current documentation **does not certify** compliance.

Official references: [FFmpeg](https://ffmpeg.org/legal.html) · [LGPLv2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) · [LGPLv3](https://www.gnu.org/licenses/lgpl-3.0.html) · [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/).
