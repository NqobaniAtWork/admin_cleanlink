from enum import Enum


class AdminRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    VERIFICATION_ADMIN = "VERIFICATION_ADMIN"
    FINANCE_ADMIN = "FINANCE_ADMIN"
    SUPPORT_ADMIN = "SUPPORT_ADMIN"


class PaymentGateway(str, Enum):
    PAYSTACK = "PAYSTACK"
    PEACH_PAYMENTS = "PEACH_PAYMENTS"
    PAYFAST = "PAYFAST"
    PAYGATE = "PAYGATE"
    OZOW = "OZOW"
    YOCO = "YOCO"


PAYMENT_GATEWAY_LABELS: dict[PaymentGateway, str] = {
    PaymentGateway.PAYSTACK: "Paystack",
    PaymentGateway.PEACH_PAYMENTS: "Peach Payments",
    PaymentGateway.PAYFAST: "PayFast",
    PaymentGateway.PAYGATE: "PayGate",
    PaymentGateway.OZOW: "Ozow",
    PaymentGateway.YOCO: "Yoco",
}

APPROVED_PAYMENT_GATEWAYS: tuple[PaymentGateway, ...] = tuple(PaymentGateway)
PRIMARY_PAYMENT_GATEWAY = PaymentGateway.PAYSTACK