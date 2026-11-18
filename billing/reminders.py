import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Invoice, PaymentReminder
from .tasks import send_sms

logger = logging.getLogger(__name__)

# Reminder policy constants
ALLOWED_START_HOUR = 9   # 09:00 AM
ALLOWED_END_HOUR = 20    # 08:00 PM
COOLDOWN_DAYS = 3        # Maximum 1 SMS per 3 days
MAX_REMINDERS = 3        # Maximum 3 reminders per invoice lifetime


def is_within_allowed_hours(now=None):
    """
    Checks if the given time is within daytime sending window (09:00 - 20:00).
    Uses the configured timezone (Asia/Kathmandu).
    """
    if now is None:
        now = timezone.localtime()
    return ALLOWED_START_HOUR <= now.hour < ALLOWED_END_HOUR


def can_send_reminder(invoice, now=None):
    """
    Validates all conditions to send an overdue payment reminder:
    1. Invoice must be overdue and in ISSUED or PARTIALLY_PAID status.
    2. Customer must have a phone number.
    3. Current time must be inside the allowed daytime window (quiet hours check).
    4. Total sent reminders must not exceed MAX_REMINDERS.
    5. At least COOLDOWN_DAYS must have passed since the last sent reminder.
    """
    if now is None:
        now = timezone.localtime()

    if invoice.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
        return False, f"Invoice status is '{invoice.status}', not overdue/unpaid."

    if invoice.due_date >= now.date():
        return False, f"Invoice is not overdue (due date: {invoice.due_date})."

    if not invoice.customer.phone:
        return False, f"Customer '{invoice.customer.name}' has no phone number on record."

    if not is_within_allowed_hours(now):
        return (
            False,
            f"Quiet hours active ({now.strftime('%H:%M')} {settings.TIME_ZONE}). "
            f"Reminders are only permitted between {ALLOWED_START_HOUR:02d}:00 and {ALLOWED_END_HOUR:02d}:00.",
        )

    sent_reminders = invoice.reminders.filter(status=PaymentReminder.Status.SENT).order_by("-sent_at")
    sent_count = sent_reminders.count()

    if sent_count >= MAX_REMINDERS:
        return False, f"Maximum reminder limit ({MAX_REMINDERS}) reached for invoice #{invoice.number}."

    last_reminder = sent_reminders.first()
    if last_reminder:
        elapsed = now - last_reminder.sent_at
        if elapsed < timedelta(days=COOLDOWN_DAYS):
            days_passed = elapsed.days
            return (
                False,
                f"Cooldown active: Reminder was sent {days_passed} day(s) ago. "
                f"Minimum interval between reminders is {COOLDOWN_DAYS} days.",
            )

    return True, "Eligible for reminder."


def send_single_reminder(invoice, now=None):
    """
    Attempts to send an SMS reminder for an individual invoice.
    """
    if now is None:
        now = timezone.localtime()

    eligible, reason = can_send_reminder(invoice, now)
    if not eligible:
        return {"invoice_id": invoice.id, "sent": False, "reason": reason}

    message = (
        f"Dear {invoice.customer.name}, your invoice #{invoice.number} from "
        f"{invoice.business.name} for Rs. {invoice.balance} was due on "
        f"{invoice.due_date:%d %b %Y}. Please clear your dues. Thank you!"
    )

    try:
        send_sms(invoice.customer.phone, message)
        reminder = PaymentReminder.objects.create(
            invoice=invoice,
            customer=invoice.customer,
            phone=invoice.customer.phone,
            message=message,
            status=PaymentReminder.Status.SENT,
            sent_at=now,
        )
        logger.info("Sent payment reminder for invoice #%s to %s", invoice.number, invoice.customer.phone)
        return {
            "invoice_id": invoice.id,
            "reminder_id": reminder.id,
            "sent": True,
            "phone": invoice.customer.phone,
            "message": message,
        }
    except Exception as exc:
        logger.error("Failed to send reminder for invoice #%s: %s", invoice.number, exc)
        PaymentReminder.objects.create(
            invoice=invoice,
            customer=invoice.customer,
            phone=invoice.customer.phone,
            message=message,
            status=PaymentReminder.Status.FAILED,
            error_message=str(exc),
            sent_at=now,
        )
        return {"invoice_id": invoice.id, "sent": False, "error": str(exc)}


def send_all_overdue_reminders(business=None, now=None):
    """
    Batch process that finds all overdue invoices and dispatches reminders
    in accordance with quiet hours, cooldown, and max retry limits.
    """
    if now is None:
        now = timezone.localtime()

    if not is_within_allowed_hours(now):
        return {
            "status": "skipped",
            "reason": (
                f"Quiet hours active ({now.strftime('%H:%M')} {settings.TIME_ZONE}). "
                f"Reminders are only permitted between {ALLOWED_START_HOUR:02d}:00 and {ALLOWED_END_HOUR:02d}:00."
            ),
            "checked": 0,
            "sent": 0,
            "skipped": 0,
            "details": [],
        }

    overdue_invoices = Invoice.objects.filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
        due_date__lt=now.date(),
    ).select_related("customer", "business")

    if business:
        overdue_invoices = overdue_invoices.filter(business=business)

    checked = 0
    sent = 0
    skipped = 0
    details = []

    for invoice in overdue_invoices:
        checked += 1
        result = send_single_reminder(invoice, now)
        if result.get("sent"):
            sent += 1
        else:
            skipped += 1
        details.append(result)

    return {
        "status": "completed",
        "checked": checked,
        "sent": sent,
        "skipped": skipped,
        "details": details,
    }
