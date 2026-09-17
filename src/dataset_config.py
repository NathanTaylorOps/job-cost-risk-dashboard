"""Shared constants for the generator and the detector, so the dataset's
"today" and its seed exist in exactly one place."""

from datetime import date, timedelta

SEED = 42

# The dataset is built on a fixed internal timeline and then shifted
# forward as a block, which keeps two things true at once.
#
# Fixed: every figure, every day-count and every relative fact in the
# README stays exactly as written, and two runs are byte-identical. A
# rolling "today" would make the seeded stories drift -- the change order
# that has been unsigned for 87 days would be a different number every
# morning, and the tests and the README would both be chasing it.
#
# Current: a portfolio piece that opens on a reporting period a year in
# the past reads as abandoned, whatever is in it. Bumping the shift moves
# the whole dataset forward without touching a single figure, because
# every date in the generator moves with it and 365 days preserves the
# month and day. That matters for the weather model, which is seasonal.
BASE_AS_OF = date(2025, 9, 30)      # the timeline the generator is written on
TIMELINE_SHIFT_DAYS = 365           # bump by 365 to move the whole snapshot on a year

AS_OF = BASE_AS_OF + timedelta(days=TIMELINE_SHIFT_DAYS)   # the dataset's fixed "today"
