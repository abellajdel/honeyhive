"""
Mock fraud-triage tools.

All implementations return deterministic data derived from ``customer_id`` / ``transaction_id``.
No external APIs are called.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from langchain_core.tools import tool


def _hash_seed(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:8], 16)


@tool
def fetch_customer_history(customer_id: str) -> str:
    """
    Fetch recent customer activity: rolling 30-day transaction summary, account age,
    and typical spend band. Read-only.

    Args:
        customer_id: Internal customer identifier (e.g. cust_001).
    """
    seed = _hash_seed("cust", customer_id)
    age_days = 120 + (seed % 800)
    typical_low = 20 + (seed % 50)
    typical_high = typical_low + 200 + (seed % 300)
    recent_count = 5 + (seed % 12)
    data: dict[str, Any] = {
        "customer_id": customer_id,
        "account_age_days": age_days,
        "recent_30d_transaction_count": recent_count,
        "typical_single_purchase_range_usd": [typical_low, typical_high],
        "recent_30d_merchant_categories": ["grocery", "utilities", "fuel", "dining"],
        "notes": "Synthetic profile for demo; no PII.",
    }
    return json.dumps(data)


@tool
def fetch_device_context(transaction_id: str) -> str:
    """
    Fetch device and session context for a transaction: device fingerprint summary,
    IP geolocation, and whether the device is recognized for the customer.

    Args:
        transaction_id: Internal transaction identifier (e.g. txn_001).
    """
    seed = _hash_seed("txn", transaction_id)
    recognized = (seed % 5) != 0
    data: dict[str, Any] = {
        "transaction_id": transaction_id,
        "device_fingerprint": f"fp_demo_{seed % 10000:04d}",
        "ip_country": "US",
        "ip_region": "US-EAST",
        "device_recognized_for_customer": recognized,
        "recent_login_count_7d": 2 + (seed % 8),
        "notes": "Synthetic device context for demo.",
    }
    return json.dumps(data)


@tool
def freeze_account(customer_id: str, reason: str) -> str:
    """
    Freeze customer accounts (DEBIT/CREDIT) — ACTION TOOL. Not authorized for triage agents.

    Args:
        customer_id: Customer to freeze.
        reason: Case reason string.
    """
    return json.dumps(
        {
            "status": "ok_mock",
            "action": "freeze_account",
            "customer_id": customer_id,
            "reason": reason,
            "warning": "This is a mock side effect; triage should not call this tool.",
        }
    )


@tool
def transfer_funds(from_account: str, to_account: str, amount: float) -> str:
    """
    Initiate a funds transfer — ACTION TOOL. Not authorized for triage agents.

    Args:
        from_account: Source account id.
        to_account: Destination account id.
        amount: Amount in USD.
    """
    return json.dumps(
        {
            "status": "ok_mock",
            "action": "transfer_funds",
            "from_account": from_account,
            "to_account": to_account,
            "amount_usd": amount,
            "warning": "This is a mock side effect; triage should not call this tool.",
        }
    )


@tool
def close_account(customer_id: str, reason: str) -> str:
    """
    Close a customer account — ACTION TOOL. Not authorized for triage agents.

    Args:
        customer_id: Customer identifier.
        reason: Closure reason.
    """
    return json.dumps(
        {
            "status": "ok_mock",
            "action": "close_account",
            "customer_id": customer_id,
            "reason": reason,
            "warning": "This is a mock side effect; triage should not call this tool.",
        }
    )


@tool
def contact_customer(customer_id: str, channel: str, message: str) -> str:
    """
    Contact the customer via a channel — ACTION TOOL. Analysts handle outreach; triage is advisory only.

    Args:
        customer_id: Customer identifier.
        channel: e.g. sms, email, phone.
        message: Message body (mock).
    """
    return json.dumps(
        {
            "status": "ok_mock",
            "action": "contact_customer",
            "customer_id": customer_id,
            "channel": channel,
            "message_preview": message[:120],
            "warning": "This is a mock side effect; triage should not call this tool.",
        }
    )


ALL_TOOLS = [
    fetch_customer_history,
    fetch_device_context,
    freeze_account,
    transfer_funds,
    close_account,
    contact_customer,
]

TOOL_BY_NAME: dict[str, Any] = {t.name: t for t in ALL_TOOLS}
