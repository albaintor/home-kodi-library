"""Real browser coverage of collection selection, uploads, provider icons and locales."""

import os
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from kodi_library.app import create_app
from kodi_library.settings import Settings


def main():
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(
            Settings(
                data_dir=Path(directory),
                api_token="collection-browser-test-token-0000",
                local_auth_networks=["127.0.0.0/8"],
            )
        )
        store = app.state.store
        store.initialize()
        sources = {
            provider: store.save_source({"name": f"Test {provider}", "type": provider})
            for provider in ("clz", "mymovies", "filmotech")
        }
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8769, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                )
                page = browser.new_page(locale="fr-FR", viewport={"width": 390, "height": 844})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://127.0.0.1:8769/", wait_until="networkidle")
                page.locator("#connections-button").click()
                for provider in sources:
                    expect(page.locator(f".connection-card .source-icon-{provider}")).to_have_count(
                        1
                    )
                form = page.locator("#collection-form")
                for provider in ("filmotech", "mymovies", "clz"):
                    form.locator('[name="collection_type"]').select_option(provider)
                    selector = form.locator('[name="source_id"]')
                    expect(selector.locator("option")).to_have_count(2)
                    expect(selector).to_have_value(sources[provider]["id"])
                    form.locator('[name="file"]').set_input_files(fixtures / f"{provider}.xml")
                    with page.expect_response(
                        lambda response, provider=provider: (
                            response.url.endswith(f"/imports/{provider}")
                            and response.request.method == "POST"
                        )
                    ) as upload:
                        form.locator('button[type="submit"]').click()
                    assert upload.value.status == 202
                    expect(form.locator('button[type="submit"]')).to_be_enabled(timeout=15000)
                page.locator('[data-close="connections-dialog"]').click()
                page.reload(wait_until="networkidle")
                for provider in ("filmotech", "mymovies"):
                    page.locator("#toggle-filters").click()
                    page.locator("#source-type").select_option(provider)
                    expect(page.locator(".media-card")).to_have_count(1)
                    page.locator(".media-card").first.click()
                    expect(
                        page.locator(f"#detail-content .source-icon-{provider}").first
                    ).to_be_visible()
                    expect(page.locator(f".edition .source-icon-{provider}")).to_have_count(1)
                    page.locator('[data-close="detail-dialog"]').click()
                    page.locator("#toggle-filters").click()
                page.locator("#connections-button").click()
                page.locator('[data-close="connections-dialog"]').click()
                page.locator("#header-language").select_option("en")
                page.wait_for_load_state("networkidle")
                page.locator("#connections-button").click()
                expect(page.locator("#collection-form")).to_contain_text("Collection type")
                assert not errors, errors
                browser.close()
                print(
                    "Collection browser checks passed (uploads, filtering, icons, mobile width, English)."
                )
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
