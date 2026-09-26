#!/usr/bin/env python
"""
Chaos Payment Provider Simulator & Invariant Verification Script

Simulates adversarial real-world payment gateway behaviors:
- Duplicate webhook deliveries (retries)
- Out-of-order events (late FAILED arriving after SUCCESS)
- Dropped webhooks (simulating network partition)
- Runs reconciliation to recover dropped payments
- Asserts core distributed invariants: zero double charges, zero illegal flips
"""

import hashlib
import hmac
import json
import os
import random
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.conf import settings
from django.core.management import call_command

from apps.bookings.models import BookingStatus
from apps.catalog.models import CentreTest
from apps.mock_provider.gateway import PaymentGateway
from apps.payments.models import Payment, PaymentStatus


def http_request(
    url: str,
    method: str = "GET",
    data: dict | None = None,
    headers: dict | None = None,
):
    headers = headers or {}
    encoded_data = None
    if data is not None:
        encoded_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            return response.status, json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        res_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(res_body)
        except Exception:
            return e.code, {"raw": res_body}


def sign_payload(payload: dict, secret: str) -> str:
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def run_chaos_simulation(base_url="http://127.0.0.1:8000", total_runs=30):
    print("=" * 70)
    print("STARTING PAYMENT CHAOS PROVIDER SIMULATOR")
    print(f"Target: {base_url} | Total Bookings: {total_runs}")
    print("=" * 70)

    # 1. Authenticate demo user
    print("\n[Step 1] Authenticating demo user...")
    status_code, auth_data = http_request(
        f"{base_url}/api/v1/auth/login/",
        method="POST",
        data={"username": "demouser", "password": "DemoPass123!"},
    )
    if status_code != 200 or "access" not in auth_data:
        print(f"[ERROR] Failed to authenticate. Status: {status_code}, data: {auth_data}")
        print("Tip: Run 'python manage.py seed_data' first.")
        return False

    token = auth_data["access"]
    auth_headers = {"Authorization": f"Bearer {token}"}
    print("[OK] Authenticated successfully.")

    # 2. Get available tests
    centre_test = CentreTest.objects.filter(is_active=True).first()
    if not centre_test:
        print("[ERROR] No active tests found. Run 'python manage.py seed_data' first.")
        return False

    # 3. Create bookings and initiate payments
    print(f"\n[Step 2] Creating {total_runs} bookings and initiating payments...")
    created_payments = []

    for i in range(total_runs):
        future_slot = (
            datetime.now(timezone.utc)
            + timedelta(days=random.randint(1, 30), hours=random.randint(1, 10))
        ).isoformat()
        b_code, b_data = http_request(
            f"{base_url}/api/v1/bookings/",
            method="POST",
            data={"centre_test": centre_test.id, "appointment_at": future_slot},
            headers=auth_headers,
        )
        if b_code != 201:
            print(f"[WARN] Failed to create booking #{i}: {b_data}")
            continue

        booking_id = b_data["id"]
        idemp_key = f"chaos_key_{uuid.uuid4().hex}"

        # Initiate payment. ASYNC_* means the provider decides but its response is lost, so the
        # payment stays PENDING and only a webhook (or reconciliation) can settle it.
        outcome = random.choices(["ASYNC_SUCCESS", "ASYNC_FAILED", "SUCCESS"], weights=[6, 2, 2])[0]
        p_code, p_data = http_request(
            f"{base_url}/api/v1/payments/",
            method="POST",
            data={"booking": booking_id, "simulate_outcome": outcome},
            headers={**auth_headers, "Idempotency-Key": idemp_key},
        )
        if p_code in (200, 201, 202):
            payment = p_data.get("payment", p_data)  # 202 wraps the payment
            created_payments.append({
                "booking_id": booking_id,
                "provider_ref": payment["provider_ref"],
                "amount": payment["amount"],
                "true_status": "SUCCESS" if outcome.endswith("SUCCESS") else "FAILED",
            })

    print(f"[OK] Created {len(created_payments)} payments.")

    # 4. Generate chaotic webhook event stream
    print("\n[Step 3] Dispatching chaotic webhook event stream...")
    webhook_queue = []
    dropped_count = 0
    dropped_refs = set()
    duplicate_count = 0

    for item in created_payments:
        dice = random.random()
        event_id = f"evt_{uuid.uuid4().hex}"

        if dice < 0.20:
            # Dropped event: simulate provider never delivering webhook
            dropped_count += 1
            dropped_refs.add(item["provider_ref"])
            continue

        # Standard event
        event = {
            "event_id": event_id,
            "provider_ref": item["provider_ref"],
            "status": item["true_status"],
            "amount": item["amount"],
        }
        webhook_queue.append(event)

        if dice < 0.60:
            # Duplicate delivery: queue identical webhook 1 to 3 times
            replays = random.randint(1, 3)
            for _ in range(replays):
                duplicate_count += 1
                webhook_queue.append(event.copy())

        elif dice < 0.80 and item["true_status"] == "SUCCESS":
            # Out-of-order: a stale FAILED racing the SUCCESS
            webhook_queue.append({
                "event_id": f"evt_late_{uuid.uuid4().hex}",
                "provider_ref": item["provider_ref"],
                "status": "FAILED",
                "amount": item["amount"],
            })

    # Shuffle the queue to simulate distributed network latency
    random.shuffle(webhook_queue)
    print(f"Queued {len(webhook_queue)} webhooks ({duplicate_count} duplicates, {dropped_count} dropped)...")

    # Dispatch webhooks over HTTP
    wh_url = f"{base_url}/api/v1/payments/webhook/"
    delivered_count = 0

    # Like a real provider: anything that isn't a 2xx is retried (up to 5 rounds)
    pending = list(webhook_queue)
    for _ in range(5):
        retry = []
        for event in pending:
            sig = sign_payload(event, settings.WEBHOOK_SECRET)
            code, _ = http_request(
                wh_url,
                method="POST",
                data=event,
                headers={"X-Webhook-Signature": sig},
            )
            if code == 200:
                delivered_count += 1
            else:
                retry.append(event)
        pending = retry
        if not pending:
            break

    print(f"[OK] Delivered {delivered_count}/{len(webhook_queue)} webhook requests with HTTP 200.")

    # 5. Run reconciliation to recover dropped events
    print("\n[Step 4] Running periodic reconciliation to recover dropped webhooks...")
    call_command("reconcile_payments", minutes=0)
    print("[OK] Reconciliation complete.")

    # 6. Verify distributed invariants
    print("\n" + "=" * 70)
    print("INVARIANT VERIFICATION REPORT")
    print("=" * 70)

    total_checked = len(created_payments)
    ledger_mismatches = 0  # our payment status disagrees with what provider recorded
    booking_mismatches = 0  # booking status doesn't follow from payment
    false_refund_flags = 0  # every booking here has 1 payment, so any refund flag is false
    still_pending = 0

    for item in created_payments:
        p = Payment.objects.select_related("booking").get(provider_ref=item["provider_ref"])
        truth = PaymentGateway.get_status(p.provider_ref)["status"]
        if p.status == PaymentStatus.PENDING:
            still_pending += 1
        elif p.status != truth:
            ledger_mismatches += 1
        expected_booking = BookingStatus.CONFIRMED if truth == "SUCCESS" else BookingStatus.FAILED
        if p.booking.status != expected_booking:
            booking_mismatches += 1
        if p.flagged_for_refund:
            false_refund_flags += 1

    reconciled = sum(1 for i in created_payments if i["provider_ref"] in dropped_refs)
    double_charge_violations = ledger_mismatches + false_refund_flags
    illegal_flips = booking_mismatches + still_pending

    print(f"  * Total Bookings Evaluated:       {total_checked}")
    print(f"  * Injected Duplicate Webhooks:     {duplicate_count}")
    print(f"  * Injected Dropped Webhooks:       {dropped_count}")
    print(f"  * Settled only by reconciliation:  {reconciled}")
    print(f"  * Payment != provider ledger:      {ledger_mismatches} (MUST BE 0)")
    print(f"  * Booking != payment outcome:      {booking_mismatches} (MUST BE 0)")
    print(f"  * Wrong refund flags:              {false_refund_flags} (MUST BE 0)")
    print(f"  * Still PENDING after reconcile:   {still_pending} (MUST BE 0)")
    print("-" * 70)

    if double_charge_violations == 0 and illegal_flips == 0:
        print("All invariants held for this run.")
        print("=" * 70)
        return True
    else:
        print("[ERROR] INVARIANT VIOLATIONS DETECTED!")
        print("=" * 70)
        return False


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    success = run_chaos_simulation(base_url=url, total_runs=count)
    sys.exit(0 if success else 1)
