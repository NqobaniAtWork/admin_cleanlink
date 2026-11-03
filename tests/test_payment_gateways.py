from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models.enums import (
    APPROVED_PAYMENT_GATEWAYS,
    PAYMENT_GATEWAY_LABELS,
    PRIMARY_PAYMENT_GATEWAY,
    PaymentGateway,
)


def test_approved_payment_gateway_codes_are_cleanlink_gateways():
    assert [gateway.value for gateway in APPROVED_PAYMENT_GATEWAYS] == [
        "PAYSTACK",
        "PEACH_PAYMENTS",
        "PAYFAST",
        "PAYGATE",
        "OZOW",
        "YOCO",
    ]


def test_paystack_is_primary_prototype_gateway():
    assert PRIMARY_PAYMENT_GATEWAY is PaymentGateway.PAYSTACK


def test_gateway_labels_match_business_names():
    assert PAYMENT_GATEWAY_LABELS[PaymentGateway.PAYSTACK] == "Paystack"
    assert PAYMENT_GATEWAY_LABELS[PaymentGateway.PEACH_PAYMENTS] == "Peach Payments"
    assert PAYMENT_GATEWAY_LABELS[PaymentGateway.PAYFAST] == "PayFast"
    assert PAYMENT_GATEWAY_LABELS[PaymentGateway.PAYGATE] == "PayGate"
    assert PAYMENT_GATEWAY_LABELS[PaymentGateway.OZOW] == "Ozow"
    assert PAYMENT_GATEWAY_LABELS[PaymentGateway.YOCO] == "Yoco"


def test_schema_seed_contains_same_gateways():
    migration = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20260911164457_initial_cleanlink_schema.sql"
    sql = migration.read_text(encoding="utf-8")
    for gateway in APPROVED_PAYMENT_GATEWAYS:
        assert gateway.value in sql
    assert "'PAYSTACK', 'Paystack', true, true" in sql
    assert "payment_attempts" in sql