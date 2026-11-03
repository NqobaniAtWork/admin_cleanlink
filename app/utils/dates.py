from datetime import date, timedelta


def calculate_subscription_period(payment_date: date) -> tuple[date, date]:
    """Return the CleanLink annual subscription period for a payment date."""

    start = payment_date.replace(day=1)
    next_year_same_month = start.replace(year=start.year + 1)
    end = next_year_same_month - timedelta(days=1)
    return start, end