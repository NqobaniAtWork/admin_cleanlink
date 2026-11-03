from app.models.enums import APPROVED_PAYMENT_GATEWAYS, PAYMENT_GATEWAY_LABELS, PaymentGateway

DEFAULT_PAGE_SIZE = 25
PAYMENT_GATEWAY_OPTIONS = [
    {"code": gateway.value, "label": PAYMENT_GATEWAY_LABELS[gateway]}
    for gateway in APPROVED_PAYMENT_GATEWAYS
]