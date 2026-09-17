import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC_DIR = os.path.join(REPO_ROOT, "src")

sys.path.insert(0, SRC_DIR)

# Pin the run to the shipped defaults before detection is imported
# anywhere. config/thresholds.example.json invites a reader to copy it to
# config/thresholds.json and change the numbers, which is the point of
# having it -- but this suite asserts the DEFAULTS (that the dollar floor
# is $5,000, that the bands sit where the README says). Without this,
# following the repo's own instructions turns the suite red, and the
# honest conclusion from a red suite is that the repo is broken.
os.environ["JOBCOST_THRESHOLDS"] = os.path.join(REPO_ROOT, "config", "__defaults_only__.json")


@pytest.fixture(scope="session", autouse=True)
def generated_data():
    """Regenerate the synthetic dataset once per test session so tests
    always run against the current generator output (both are seeded with
    the same fixed RNG seed, so this is deterministic and reproducible)."""
    subprocess.run(
        [sys.executable, os.path.join(SRC_DIR, "generate_data.py")],
        check=True, cwd=REPO_ROOT, capture_output=True,
    )
    yield


@pytest.fixture
def data():
    """Fresh DataFrames per test, so no test can leak an in-place edit into
    another (loading eleven small CSVs is cheap)."""
    import detection
    return detection.load_data()
