"""Chromium checks with synthetic data, also under a Home Assistant URL prefix."""

import json
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import uvicorn
from fastapi import FastAPI
from playwright.sync_api import expect, sync_playwright

from kodi_library.app import create_app
from kodi_library.connectors.base import Record
from kodi_library.settings import Settings
from kodi_library.store import Store


def main():
    with tempfile.TemporaryDirectory() as directory:
        data = Path(directory)
        store = Store(data / "catalogue.db")
        store.initialize()
        source = store.save_source({"name": "Kodi de démonstration", "type": "kodi"})
        store.apply_snapshot(
            source["id"],
            [
                Record(
                    f"movie:{index}",
                    "movie",
                    f"Film de démonstration {index:03}",
                    genres=["Drame" if index < 80 else "Aventure"],
                    identifiers={"imdb": f"tt123{index:04d}"},
                    year=2025,
                    runtime_minutes=125,
                    rating=8.1,
                    plot="Une équipe part à la découverte d'une cité oubliée. " * 6,
                    formats=["4K", "1080p", "3D", "Dolby Vision", "HDR10", "HDR10+", "Dolby Atmos"],
                    poster=["https://example.org/demo.jpg"],
                    metadata={
                        "kodi_id": index,
                        "file": f"smb://nas/films/film-{index}.mkv",
                        "is_default": True,
                        "cast": [
                            {
                                "name": f"Interprète Exemple {n}",
                                "role": f"Personnage {n}",
                                "order": n,
                            }
                            for n in range(12)
                        ],
                    },
                )
                for index in range(150)
            ],
        )
        store.save_player(
            {
                "name": "Kodi Salon",
                "source_id": source["id"],
                "host": "kodi.example",
                "tls": False,
                "port": 8080,
                "username": "",
            }
        )
        app = create_app(
            Settings(
                data_dir=data,
                api_token="browser-test-admin-token-000000000",
                ingress_token="browser-test-ingress-token-0000000",
                scheduler_enabled=False,
                tmdb_api_token="synthetic-browser-tmdb-token",
            )
        )

        enriched_calls = []
        play_calls = []

        async def enrichment(detail, _token):
            enriched_calls.append(detail["id"])
            return {
                "people": [
                    *(
                        {
                            "name": f"Interprète Exemple {n}",
                            "role": f"Personnage {n}",
                            "category": "cast",
                            "tmdb_id": str(100 + n),
                            "photos": ["https://example.org/portrait.jpg"],
                        }
                        for n in range(12)
                    ),
                    {
                        "name": "Camille Musique",
                        "category": "composer",
                        "role": "Original Music Composer",
                        "tmdb_id": "500",
                        "photos": ["https://example.org/portrait.jpg"],
                    },
                ],
                "trailers": ["https://www.youtube.com/watch?v=C6PbWhWGUrY"],
            }

        async def playback(player, detail, edition_id, resume):
            play_calls.append((player["id"], detail["id"], edition_id, resume))
            return {
                "player_id": player["id"],
                "media_id": detail["id"],
                "edition_id": edition_id,
                "status": "started",
            }

        enrichment_patch = patch("kodi_library.app.enrich_tmdb", side_effect=enrichment)
        playback_patch = patch("kodi_library.app.play", side_effect=playback)
        enrichment_patch.start()
        playback_patch.start()

        @asynccontextmanager
        async def lifespan(_root):
            async with app.router.lifespan_context(app):
                yield

        root = FastAPI(lifespan=lifespan)

        @root.middleware("http")
        async def ingress(request, call_next):
            request.scope["headers"].append(
                (b"x-kodi-library-ingress-token", b"browser-test-ingress-token-0000000")
            )
            return await call_next(request)

        root.mount("/api/ingress/kodi_library", app)
        server = uvicorn.Server(
            uvicorn.Config(
                root, host="127.0.0.1", port=8768, log_level="error", proxy_headers=False
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "request",
                    lambda request: (
                        requests.append(parse_qs(urlsplit(request.url).query))
                        if urlsplit(request.url).path.endswith("/api/v1/media")
                        else None
                    ),
                )
                page.route(
                    "**/artwork/**",
                    lambda route: route.fulfill(
                        content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900"><rect width="600" height="900" fill="#30415b"/><text x="50" y="480" fill="#bcdb80" font-size="70">CINÉMA</text></svg>',
                    ),
                )
                page.route(
                    "**/people/*/artwork",
                    lambda route: route.fulfill(
                        content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="140"><rect width="100" height="140" fill="#30415b"/><circle cx="50" cy="47" r="25" fill="#b2a5ef"/><path d="M10 140V110Q50 55 90 110V140" fill="#bcdb80"/></svg>',
                    ),
                )
                page.route(
                    "https://www.themoviedb.org/assets/**",
                    lambda route: route.fulfill(
                        content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="30"><text y="25" fill="#01b4e4">TMDB</text></svg>',
                    ),
                )
                prefix = "http://127.0.0.1:8768/api/ingress/kodi_library/"
                page.goto(prefix, wait_until="domcontentloaded")
                expect(page.locator(".media-card")).to_have_count(60)
                first = page.locator(".media-card").first.element_handle()
                page.locator("#load-more-trigger").scroll_into_view_if_needed()
                expect(page.locator(".media-card")).to_have_count(120)
                assert first.evaluate("element=>element===document.querySelector('.media-card')")
                assert page.evaluate("scrollY") > 0
                page.locator("#load-more-trigger").scroll_into_view_if_needed()
                expect(page.locator(".media-card")).to_have_count(150)
                expect(page.locator("#load-more")).to_be_hidden()
                expect(page.locator("#load-more-status")).to_contain_text("Tous les résultats")
                assert [r["offset"][0] for r in requests] == ["0", "60", "120"]
                ids = page.locator(".media-card").evaluate_all(
                    "elements=>elements.map(element=>element.dataset.media)"
                )
                assert len(set(ids)) == 150
                page.locator("#list-view").click()
                expect(page.locator(".media-card")).to_have_count(150)
                page.locator("#genre").select_option("Drame")
                expect(page.locator(".media-card")).to_have_count(60)
                page.locator("#load-more-trigger").scroll_into_view_if_needed()
                expect(page.locator(".media-card")).to_have_count(80)
                assert requests[-1]["genre"] == ["Drame"] and requests[-1]["offset"] == ["60"]
                failed = [False]

                def fail_once(route):
                    query = parse_qs(urlsplit(route.request.url).query)
                    if query.get("offset") == ["60"] and not query.get("genre") and not failed[0]:
                        failed[0] = True
                        return route.fulfill(
                            status=503,
                            content_type="application/json",
                            body=json.dumps({"error": {"message": "Erreur temporaire simulée"}}),
                        )
                    return route.continue_()

                page.route("**/api/v1/media?*", fail_once)
                page.locator("#reset-filters").click()
                expect(page.locator(".media-card")).to_have_count(60)
                page.locator("#load-more-trigger").scroll_into_view_if_needed()
                expect(page.locator("#load-more")).to_have_text("Réessayer")
                expect(page.locator(".media-card")).to_have_count(60)
                page.locator("#load-more").click()
                expect(page.locator(".media-card")).to_have_count(120)
                page.unroute("**/api/v1/media?*", fail_once)
                pending = []

                def delay_last_page(route):
                    if parse_qs(urlsplit(route.request.url).query).get("offset") == ["120"]:
                        pending.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/media?*", delay_last_page)
                with page.expect_request("**/api/v1/media?*offset=120*"):
                    page.locator("#load-more-trigger").scroll_into_view_if_needed()
                page.locator("#genre").select_option("Aventure")
                expect(page.locator(".media-card")).to_have_count(60)
                for route in pending:
                    try:
                        route.fulfill(
                            content_type="application/json",
                            body=json.dumps({"items": [], "total": 150, "has_more": False}),
                        )
                    except Exception:
                        pass  # The obsolete fetch may already have been aborted.
                expect(page.locator("#result-count")).to_contain_text("70 résultats")
                page.unroute("**/api/v1/media?*", delay_last_page)
                page.locator("#grid-view").click()
                page.locator(".media-card").first.click()
                expect(page.locator("#detail-dialog")).to_be_visible()
                expect(page.locator(".cast-member")).to_have_count(13)
                expect(page.locator(".person-photo img")).to_have_count(13)
                expect(page.locator(".person-photo img").first).to_have_js_property(
                    "complete", True
                )
                assert page.locator(".person-photo img").first.evaluate(
                    "image=>image.naturalWidth>0"
                )
                expect(
                    page.locator('.person-links a[href="https://www.themoviedb.org/person/100"]')
                ).to_have_count(1)
                expect(page.locator(".credits-section").last).to_contain_text("MUSIQUE")
                expect(page.locator(".detail-actions a").first).to_have_attribute(
                    "href", "https://www.youtube.com/watch?v=C6PbWhWGUrY"
                )
                expect(page.locator(".file-row code")).to_contain_text("smb://nas/films/")
                selected_edition = page.locator("#play-edition").input_value()
                page.locator("#play-button").click()
                expect(page.locator("#toast")).to_contain_text("Lecture envoyée")
                assert play_calls[-1][2] == selected_edition
                page.locator('#personal-form input[name="user_rating"]').fill("8.5")
                page.locator('#personal-form select[name="watched"]').select_option("true")
                page.locator('#personal-form button[type="submit"]').click()
                expect(page.locator(".detail-intro .status-chip")).to_have_text("Vu")
                expect(page.locator(".detail-info")).to_contain_text("Votre note : 8.5/10")
                page.locator("#add-to-list").click()
                page.locator("#choose-new-list input").fill("À voir ensemble")
                page.locator("#choose-new-list button").click()
                expect(page.locator("#choose-list-dialog")).not_to_be_visible()
                expect(page.locator(".detail-intro")).to_contain_text("À voir ensemble")
                initial_calls = len(enriched_calls)
                page.locator("#refresh-metadata").click()
                expect(page.locator("#refresh-metadata")).to_be_enabled()
                assert len(enriched_calls) == initial_calls + 1
                expect(page.locator(".cast-member").first).to_contain_text("Personnage 0")
                expect(page.locator(".detail-intro .format-badge")).to_have_count(7)
                intro = page.locator(".detail-intro").bounding_box()
                body = page.locator(".detail-body").bounding_box()
                assert abs(intro["x"] - body["x"]) < 2 and body["width"] > 500
                page.locator(".edition summary").click()
                expect(page.locator(".edition-formats")).to_be_visible()
                assert page.request.get(prefix + "static/cinema.svg").status == 200
                assert page.request.get(prefix + "static/format-logos.svg").status == 200
                screenshots = os.getenv("UI_SCREENSHOT_DIR")
                page.locator("#detail-dialog").evaluate("element=>element.scrollTop=0")
                if screenshots:
                    Path(screenshots).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(screenshots) / "detail-desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("#detail-dialog").evaluate(
                    "element=>element.scrollWidth<=element.clientWidth"
                )
                mobile_body = page.locator(".detail-body").bounding_box()
                mobile_intro = page.locator(".detail-intro").bounding_box()
                assert mobile_body["width"] > 300 and mobile_body["y"] > mobile_intro["y"]
                if screenshots:
                    page.screenshot(path=str(Path(screenshots) / "detail-mobile.png"))
                page.locator('[data-close="detail-dialog"]').click()
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator("#lists-button").click()
                expect(page.locator(".saved-list-row")).to_have_count(1)
                page.locator("#list-add-button").click()
                page.locator("#list-search").fill("démonstration 001")
                expect(page.locator(".search-list-result")).to_have_count(1)
                page.locator("[data-add-list-media]").click()
                expect(page.locator(".saved-list-row")).to_have_count(2)
                page.locator('.saved-row-actions [data-direction="-1"]').nth(1).click()
                expect(page.locator(".saved-list-row").first).to_contain_text("001")
                page.locator("#list-sort").select_option("title")
                expect(page.locator("[data-move-list]")).to_have_count(0)
                page.locator("#list-sort").select_option("position")
                expect(page.locator(".saved-list-row").first).to_contain_text("001")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator("#lists-dialog").evaluate("e=>e.scrollWidth<=e.clientWidth")
                if screenshots:
                    page.screenshot(path=str(Path(screenshots) / "lists-mobile.png"))
                page.locator(".saved-list-row").first.locator("[data-remove-list-media]").click()
                expect(page.locator(".saved-list-row")).to_have_count(1)
                page.locator('[data-close="lists-dialog"]').click()
                # Opening the cached film again does not contact TMDB.
                page.locator(".media-card").first.click()
                expect(page.locator("#detail-dialog")).to_be_visible()
                assert len(enriched_calls) == initial_calls + 1
                page.locator('[data-close="detail-dialog"]').click()
                fallback = browser.new_page(viewport={"width": 1440, "height": 1000})
                fallback.add_init_script("delete window.IntersectionObserver")
                fallback.goto(prefix, wait_until="domcontentloaded")
                expect(fallback.locator(".media-card")).to_have_count(60)
                fallback.locator("#load-more").click()
                expect(fallback.locator(".media-card")).to_have_count(120)
                assert not errors, errors
                browser.close()
                print(
                    "Browser checks passed: scroll batches, preserved cards/position, filters, list/grid, retry, cancellation, mobile/desktop detail, casting/portraits/TMDB cache/refresh, personal edits, ordered lists/search, Kodi edition playback/path, format logos, favicon, manual fallback, Ingress prefix."
                )
        finally:
            enrichment_patch.stop()
            playback_patch.stop()
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
