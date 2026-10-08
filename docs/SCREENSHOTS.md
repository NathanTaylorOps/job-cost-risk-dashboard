# Capturing genuine dashboard screenshots

Screenshots in this repository should come from the working Streamlit application, not generated artwork or static mockups. Use the seeded fictional portfolio and verify that no uploaded client data is present before capture.

From the repository root, install the development dependencies and Chromium:

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

In one terminal, start the application:

```bash
streamlit run app/streamlit_app.py
```

In another terminal:

```bash
python scripts/capture_screenshot.py
```

This saves `docs/portfolio-overview.png` using a 1440 × 960 browser viewport with a full-page screenshot. The screenshot contains the *rendered application*, including its fictional data. Review the captured image before adding it to the README; full-page captures can be too tall for a readable README hero.

For a cropped, purpose-specific second image, navigate to the relevant job in the browser and use your browser's screenshot tool or inspect the running app manually. Keep the crop focused on the project detail, and save it as `docs/project-detail.png` if needed.

Capture utility options:

```bash
python scripts/capture_screenshot.py --url http://localhost:8501 --output docs/portfolio-overview.png --width 1440 --height 960
```

After the screenshots are inspected and committed, add one overview image near the top of the README and an optional project-detail image next to the demonstration cases. Do not add image references before the files exist.

## Clean README overview capture

Streamlit may display a one-time suggestion about installing development skills. The capture utility now dismisses that suggestion through the visible UI before taking a screenshot.

For a focused image showing the main portfolio screen rather than the entire sidebar, run:

```bash
python scripts/capture_screenshot.py --main-only --output docs/portfolio-overview.png --width 1440 --height 960
```

Open the output at full resolution and check that the portfolio grid is readable and that no popup obscures information. Do not publish screenshots containing a local help popup. The original full-page mode remains available without `--main-only`.
