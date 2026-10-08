"""Capture an actual browser screenshot of the running dashboard.

Start Streamlit separately:
    streamlit run app/streamlit_app.py

Then:
    python scripts/capture_screenshot.py

The output is a real browser capture (not an illustrative mockup).
Requires Playwright and Chromium from requirements-dev.txt.
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--output", default="docs/portfolio-overview.png")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=960)
    parser.add_argument("--main-only", action="store_true",
                        help="Capture only the main dashboard, without the sidebar.")
    args = parser.parse_args()

    if args.width < 640 or args.height < 480:
        parser.error("Viewport must be at least 640x480.")

    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": args.width, "height": args.height},
                device_scale_factor=1,
                reduced_motion="reduce",
            )
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            page.locator('[data-testid="stApp"]').wait_for(timeout=60000)
            page.get_by_text("Ridgeline Custom Homes").first.wait_for(timeout=60000)
            page.wait_for_load_state("networkidle", timeout=60000)
            # Streamlit may show its own onboarding recommendation on the
            # first launch. Dismiss it through the visible UI before capture.
            dismiss = page.get_by_text("Don't show again", exact=True)
            if dismiss.count() and dismiss.first.is_visible():
                dismiss.first.click()
                page.wait_for_timeout(300)

            if args.main_only:
                # Keeps the risk grid legible in the README without modifying
                # any application values, classes, or visual content.
                page.locator('[data-testid="stMain"]').screenshot(
                    path=str(target), animations="disabled"
                )
            else:
                page.screenshot(
                    path=str(target), full_page=True, animations="disabled"
                )
        finally:
            browser.close()

    print(f"Captured real dashboard to {target.resolve()}")


if __name__ == "__main__":
    main()
