"""Chromium checks with synthetic data, also under a Home Assistant URL prefix."""

import json
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
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
                    year=2025,
                    runtime_minutes=125,
                    rating=8.1,
                    plot="Une équipe part à la découverte d'une cité oubliée. " * 6,
                    formats=["4K", "1080p", "3D", "Dolby Vision", "HDR10", "HDR10+", "Dolby Atmos"],
                    poster=["https://example.org/demo.jpg"],
                    metadata={
                        "cast": [
                            {
                                "name": f"Interprète Exemple {n}",
                                "role": f"Personnage {n}",
                                "order": n,
                            }
                            for n in range(12)
                        ]
                    },
                )
                for index in range(150)
            ],
        )
        app = create_app(
            Settings(
                data_dir=data,
                api_token="browser-test-admin-token-000000000",
                ingress_token="browser-test-ingress-token-0000000",
                scheduler_enabled=False,
            )
        )

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
            uvicorn.Config(root, host="127.0.0.1", port=8768, log_level="error")
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
                expect(page.locator(".cast-member")).to_have_count(12)
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
                fallback = browser.new_page(viewport={"width": 1440, "height": 1000})
                fallback.add_init_script("delete window.IntersectionObserver")
                fallback.goto(prefix, wait_until="domcontentloaded")
                expect(fallback.locator(".media-card")).to_have_count(60)
                fallback.locator("#load-more").click()
                expect(fallback.locator(".media-card")).to_have_count(120)
                assert not errors, errors
                browser.close()
                print(
                    "Browser checks passed: scroll batches, preserved cards/position, filters, list/grid, retry, cancellation, mobile/desktop detail, casting, format logos, favicon, manual fallback, Ingress prefix."
                )
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
