from datetime import date
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.utils.dates import calculate_subscription_period


def test_july_subscription_period_expires_end_of_june_next_year():
    start, end = calculate_subscription_period(date(2025, 7, 15))
    assert start == date(2025, 7, 1)
    assert end == date(2026, 6, 30)


def test_march_subscription_period_handles_february_end_date():
    start, end = calculate_subscription_period(date(2026, 3, 3))
    assert start == date(2026, 3, 1)
    assert end == date(2027, 2, 28)