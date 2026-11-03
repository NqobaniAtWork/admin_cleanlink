from unittest.mock import MagicMock
import pytest

from app.services.refund_service import RefundService


def create_mock_client():
    client = MagicMock()
    tables = {}

    def get_table(name):
        if name not in tables:
            tables[name] = MagicMock()
        return tables[name]

    client.table.side_effect = get_table
    return client, tables


def test_count_metrics():
    client, _ = create_mock_client()
    mock_refunds = [
        {"id": "r1", "amount": 150.0, "status": "APPROVED", "created_at": "2026-11-01T10:00:00Z"},
        {"id": "r2", "amount": 200.0, "status": "COMPLETED", "created_at": "2026-11-01T11:00:00Z"},
        {"id": "r3", "amount": 100.0, "status": "REQUESTED", "created_at": "2026-11-01T12:00:00Z"},
        {"id": "r4", "amount": 50.0, "status": "REJECTED", "created_at": "2026-11-01T13:00:00Z"},
    ]

    service = RefundService(client)
    service.refunds.list_refunds = MagicMock(return_value=mock_refunds)

    metrics = service.count_metrics()
    assert metrics["all"] == 4
    assert metrics["pending"] == 1
    assert metrics["approved"] == 2
    assert metrics["rejected"] == 1
    assert metrics["total_refunded_zar"] == 350.0


def test_list_refundable_payments():
    client, _ = create_mock_client()
    service = RefundService(client)

    service.payments.list_recent = MagicMock(return_value=[
        {"id": "p1", "amount": 500.0, "status": "PAID", "payer_name": "Alice"},
        {"id": "p2", "amount": 300.0, "status": "PAID", "payer_name": "Bob"},
        {"id": "p3", "amount": 250.0, "status": "FAILED", "payer_name": "Charlie"},
    ])

    service.refunds.get_refunds_for_payment = MagicMock(side_effect=lambda pid: [
        {"amount": 200.0, "status": "APPROVED"}
    ] if pid == "p1" else [
        {"amount": 300.0, "status": "APPROVED"}
    ] if pid == "p2" else [])

    refundable = service.list_refundable_payments()
    assert len(refundable) == 1
    assert refundable[0]["id"] == "p1"
    assert refundable[0]["remaining_refundable"] == 300.0


def test_issue_refund_auto_approved_full():
    client, tables = create_mock_client()
    service = RefundService(client)

    service.payments.get_by_id = MagicMock(return_value={
        "id": "p1",
        "booking_id": "b1",
        "payer_id": "client-1",
        "payee_id": "cleaner-1",
        "amount": 500.0,
        "cleaner_net_amount": 425.0,
        "status": "PAID",
    })
    service.refunds.get_refunds_for_payment = MagicMock(return_value=[])
    service.refunds.create = MagicMock(return_value={"id": "new-ref-1"})

    created = service.issue_refund(
        admin_user_id="admin-1",
        payment_id="p1",
        amount=500.0,
        reason="Cleaner no show",
        auto_approve=True,
    )

    assert created["id"] == "new-ref-1"
    # Payments updated to REFUNDED
    update_mock = tables["payments"].update
    assert update_mock.call_args[0][0]["status"] == "REFUNDED"

    # Cleaner ledger debited 425.0
    ledger_insert_mock = tables["cleaner_ledger_entries"].insert
    ledger_payload = ledger_insert_mock.call_args[0][0]
    assert ledger_payload["cleaner_id"] == "cleaner-1"
    assert ledger_payload["direction"] == "DEBIT"
    assert ledger_payload["amount"] == 425.0
    assert ledger_payload["transaction_type"] == "REFUND"


def test_issue_refund_auto_approved_partial():
    client, tables = create_mock_client()
    service = RefundService(client)

    service.payments.get_by_id = MagicMock(return_value={
        "id": "p1",
        "booking_id": "b1",
        "payer_id": "client-1",
        "payee_id": "cleaner-1",
        "amount": 500.0,
        "cleaner_net_amount": 425.0,
        "status": "PAID",
    })
    service.refunds.get_refunds_for_payment = MagicMock(return_value=[])
    service.refunds.create = MagicMock(return_value={"id": "new-ref-2"})

    service.issue_refund(
        admin_user_id="admin-1",
        payment_id="p1",
        amount=250.0,
        reason="Partial service complaint",
        auto_approve=True,
    )

    update_mock = tables["payments"].update
    assert update_mock.call_args[0][0]["status"] == "PARTIALLY_REFUNDED"

    ledger_insert_mock = tables["cleaner_ledger_entries"].insert
    ledger_payload = ledger_insert_mock.call_args[0][0]
    assert ledger_payload["amount"] == 212.50
    assert ledger_payload["direction"] == "DEBIT"


def test_issue_refund_exceeds_refundable_amount_raises_error():
    client, _ = create_mock_client()
    service = RefundService(client)

    service.payments.get_by_id = MagicMock(return_value={
        "id": "p1",
        "amount": 300.0,
        "status": "PAID",
    })
    service.refunds.get_refunds_for_payment = MagicMock(return_value=[
        {"amount": 200.0, "status": "APPROVED"}
    ])

    with pytest.raises(ValueError, match="exceeds remaining refundable balance"):
        service.issue_refund(
            admin_user_id="admin-1",
            payment_id="p1",
            amount=150.0,
            reason="Too much",
        )


def test_approve_refund():
    client, tables = create_mock_client()
    service = RefundService(client)

    service.refunds.get_by_id = MagicMock(return_value={
        "id": "ref-1",
        "payment_id": "p1",
        "amount": 400.0,
        "status": "REQUESTED",
    })
    service.payments.get_by_id = MagicMock(return_value={
        "id": "p1",
        "booking_id": "b1",
        "payee_id": "cleaner-1",
        "amount": 400.0,
        "cleaner_net_amount": 340.0,
        "status": "PAID",
    })
    service.refunds.get_refunds_for_payment = MagicMock(return_value=[
        {"id": "ref-1", "amount": 400.0, "status": "REQUESTED"}
    ])
    service.refunds.update_status = MagicMock()

    service.approve_refund(admin_user_id="admin-1", refund_id="ref-1")

    service.refunds.update_status.assert_called_once()
    assert tables["payments"].update.call_args[0][0]["status"] == "REFUNDED"


def test_reject_refund():
    client, _ = create_mock_client()
    service = RefundService(client)

    service.refunds.get_by_id = MagicMock(return_value={
        "id": "ref-99",
        "status": "REQUESTED",
    })
    service.refunds.update_status = MagicMock()

    service.reject_refund(admin_user_id="admin-1", refund_id="ref-99", reason="Policy violation")
    service.refunds.update_status.assert_called_once()
    assert service.refunds.update_status.call_args[0][1] == "REJECTED"
