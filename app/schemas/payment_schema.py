from pydantic import BaseModel, Field

from app.models.enums import PaymentGateway


class PaymentGatewayView(BaseModel):
    code: PaymentGateway
    name: str
    active: bool = True


class RefundDecision(BaseModel):
    refund_id: str
    reason: str = Field(min_length=5, max_length=500)