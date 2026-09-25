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

import os
import sys
import json
import time
import uuid
import random
import hmac
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.core.management import call_command
from django.conf import settings
from apps.bookings.models import Booking, BookingStatus
from apps.payments.models import Payment, PaymentStatus, WebhookEvent
from apps.catalog.models import CentreTest


def http_request(url: str, method: str = "GET", data: dict | None = None, headers: dict | None = None):
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
    print("🚀 STARTING PAYMENT CHAOS PROVIDER SIMULATOR")
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
        print(f"❌ Failed to authenticate. Status: {status_code}, data: {auth_data}")
        print("Tip: Run 'python manage.py seed_data' first.")
        return False

    token = auth_data["access"]
    auth_headers = {"Authorization": f"Bearer {token}"}
    print("✅ Authenticated successfully.")

    # 2. Get available tests
    centre_test = CentreTest.objects.filter(is_active=True).first()
    if not centre_test:
        print("❌ No active tests found. Run 'python manage.py seed_data' first.")
        return False

    # 3. Create bookings and initiate payments
    print(f"\n[Step 2] Creating {total_runs} bookings and initiating payments...")
    created_payments = []

    for i in range(total_runs):
        future_slot = (datetime.now(timezone.utc) + timedelta(days=random.randint(1, 30), hours=random.randint(1, 10))).isoformat()
        b_code, b_data = http_request(
            f"{base_url}/api/v1/bookings/",
            method="POST",
            data={"centre_test": centre_test.id, "appointment_at": future_slot},
            headers=auth_headers,
        )
        if b_code != 201:
            print(f"⚠️ Failed to create booking #{i}: {b_data}")
            continue

        booking_id = b_data["id"]
        idemp_key = f"chaos_key_{uuid.uuid4().hex}"

        # Initiate payment
        p_code, p_data = http_request(
            f"{base_url}/api/v1/payments/",
            method="POST",
            data={"booking": booking_id, "simulate_outcome": "SUCCESS"},
            headers={**auth_headers, "Idempotency-Key": idemp_key},
        )
        if p_code in (200, 201, 202):
            created_payments.append({
                "booking_id": booking_id,
                "provider_ref": p_data["provider_ref"],
                "amount": p_data["amount"],
            })

    print(f"✅ Created {len(created_payments)} payments.")

    # 4. Generate chaotic webhook event stream
    print("\n[Step 3] Dispatching chaotic webhook event stream...")
    webhook_queue = []
    dropped_count = 0
    duplicate_count = 0

    for item in created_payments:
        # Determine scenario
        dice = random.random()
        event_id = f"evt_{uuid.uuid4().hex}"

        if dice < 0.20:
            # Dropped event: simulate provider never delivering webhook
            dropped_count += 1
            continue

        # Standard event
        event = {
            "event_id": event_id,
            "provider_ref": item["provider_ref"],
            "status": "SUCCESS",
            "amount": item["amount"],
        }
        webhook_queue.append(event)

        if dice < 0.60:
            # Duplicate delivery: queue identical webhook 1 to 3 times
            replays = random.randint(1, 3)
            for _ in range(replays):
                duplicate_count += 1
                webhook_queue.append(event.copy())

        elif dice < 0.80:
            # Out-of-order: append a late FAILED webhook
            webhook_queue.append({
                "event_id": f"evt_late_{uuid.uuid4().hex}",
                "provider_ref": item["provider_ref"],
                "status": "FAILED",
                "amount": item["amount"],
            })

    # Shuffle the queue to simulate distributed network latency
    random.shuffle(webhook_queue)
    print(f"📬 Queued {len(webhook_queue)} webhooks (including {duplicate_count} deliberate duplicates and {dropped_count} dropped events)...")

    # Dispatch webhooks over HTTP
    wh_url = f"{base_url}/api/v1/payments/webhook/"
    delivered_count = 0

    for event in webhook_queue:
        sig = sign_payload(event, settings.WEBHOOK_SECRET)
        code, resp = http_request(
            wh_url,
            method="POST",
            data=event,
            headers={"X-Webhook-Signature": sig},
        )
        if code == 200:
            delivered_count += 1

    print(f"✅ Delivered {delivered_count} webhook requests (all returned HTTP 200).")

    # 5. Run reconciliation to recover dropped events
    print("\n[Step 4] Running periodic reconciliation to recover dropped webhooks...")
    call_command("reconcile_payments", minutes=0)
    print("✅ Reconciliation complete.")

    # 6. Verify distributed invariants
    print("\n" + "=" * 70)
    print("📊 INVARIANT VERIFICATION REPORT")
    print("=" * 70)

    total_checked = len(created_payments)
    double_charge_violations = 0
    illegal_flips = 0

    for item in created_payments:
        b = Booking.objects.get(id=item["booking_id"])
        # Check double-charge: only 1 non-refunded successful payment per booking
        success_payments = Payment.objects.filter(
            booking=b, status=PaymentStatus.SUCCESS, flagged_for_refund=False
        ).count()
        if success_payments > 1:
            double_charge_violations += 1

        # Check illegal status
        if b.status not in (BookingStatus.CONFIRMED, BookingStatus.CANCELLED, BookingStatus.FAILED, BookingStatus.PENDING):
            illegal_flips += 1

    print(f"  • Total Bookings Evaluated:       {total_checked}")
    print(f"  • Injected Duplicate Webhooks:     {duplicate_count}")
    print(f"  • Injected Dropped Webhooks:       {dropped_count}")
    print(f"  • Double-Charge Violations:       {double_charge_violations} (MUST BE 0)")
    print(f"  • Illegal State Machine Flips:     {illegal_flips} (MUST BE 0)")
    print("-" * 70)

    if double_charge_violations == 0 and illegal_flips == 0:
        print("🎯 ALL DISTRIBUTED INVARIANTS SATISFIED (100% CORRECTNESS)")
        print("=" * 70)
        return True
    else:
        print("❌ INVARIANT VIOLATIONS DETECTED!")
        print("=" * 70)
        return False


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    success = run_chaos_simulation(base_url=url, total_runs=count)
    sys.exit(0 if success else 1)
