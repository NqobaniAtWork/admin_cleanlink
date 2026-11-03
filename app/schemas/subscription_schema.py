from datetime import date

from pydantic import BaseModel


class SubscriptionPeriod(BaseModel):
    start_date: date
    end_date: date


class SubscriptionActivation(BaseModel):
    cleaner_id: str
    payment_id: str
    plan_id: str
    payment_date: date
    reason: str