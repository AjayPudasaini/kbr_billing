from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Max, Sum

from .exceptions import InvalidReturn
from .models import CreditNote, CreditNoteItem, Invoice, Product

VAT_RATE = Decimal("0.13")
TWO_PLACES = Decimal("0.01")


def next_credit_note_number(business):
    last = CreditNote.objects.filter(business=business).aggregate(n=Max("number"))["n"]
    return (last or 0) + 1


@transaction.atomic
def process_return(*, invoice, items, reason="", refund_method=CreditNote.RefundMethod.CREDIT, user=None):
    """
    Process a customer return for an invoice:
    - Validates returned items and quantities against original invoice and prior returns.
    - Locks products and restocks inventory.
    - Reverses 13% VAT and calculates credit totals.
    - Adjusts invoice total, tax, subtotal, amount_paid, and status.
    - Creates and returns a CreditNote accounting document.
    """
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
        raise InvalidReturn(f"Cannot process return for an invoice with status '{invoice.status}'.")

    if not items:
        raise InvalidReturn("At least one item must be returned.")

    business = invoice.business
    credit_note_number = next_credit_note_number(business)

    # Map invoice items by product_id
    invoice_items_by_product = {
        item.product_id: item for item in invoice.items.select_related("product").all()
    }

    # Aggregate quantities previously returned on this invoice by product
    prior_returns_qs = (
        CreditNoteItem.objects.filter(credit_note__invoice=invoice)
        .values("product_id")
        .annotate(total_returned=Sum("quantity"))
    )
    already_returned_by_product = {
        row["product_id"]: row["total_returned"] for row in prior_returns_qs
    }

    validated_lines = []
    subtotal = Decimal("0.00")

    for row in items:
        product_id = row.get("product_id")
        quantity = row.get("quantity", 0)

        if not product_id or quantity <= 0:
            raise InvalidReturn("Each return item must have a valid product_id and quantity > 0.")

        invoice_item = invoice_items_by_product.get(product_id)
        if not invoice_item:
            raise InvalidReturn(f"Product ID {product_id} was not part of invoice #{invoice.number}.")

        already_returned = already_returned_by_product.get(product_id, 0)
        remaining_returnable = invoice_item.quantity - already_returned

        if quantity > remaining_returnable:
            raise InvalidReturn(
                f"Cannot return {quantity} of {invoice_item.product.name} (SKU: {invoice_item.product.sku}). "
                f"Only {remaining_returnable} remaining returnable ({already_returned} previously returned out of {invoice_item.quantity})."
            )

        # Restock product with row-level lock
        product = Product.objects.select_for_update().get(pk=product_id, business=business)
        product.stock += quantity
        product.save(update_fields=["stock"])

        line_total = (invoice_item.unit_price * quantity).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        subtotal += line_total

        validated_lines.append(
            {
                "product": product,
                "quantity": quantity,
                "unit_price": invoice_item.unit_price,
                "line_total": line_total,
            }
        )

    tax = (subtotal * VAT_RATE).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total = subtotal + tax

    # Determine refund allocation based on customer's current balance vs amount already paid
    current_balance = invoice.balance
    if current_balance >= total:
        # The invoice had enough unpaid balance to absorb the credit entirely
        refund_amount = Decimal("0.00")
    else:
        # Some or all of the returned items were already paid for
        refund_amount = (total - max(Decimal("0.00"), current_balance)).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

    # Adjust invoice totals
    invoice.subtotal = max(Decimal("0.00"), invoice.subtotal - subtotal)
    invoice.tax = max(Decimal("0.00"), invoice.tax - tax)
    invoice.total = max(Decimal("0.00"), invoice.total - total)

    # If money is refunded back to customer (cash/bank), reduce invoice amount_paid accordingly
    if refund_amount > 0 and refund_method in (CreditNote.RefundMethod.CASH, CreditNote.RefundMethod.BANK):
        invoice.amount_paid = max(Decimal("0.00"), invoice.amount_paid - refund_amount)

    # Update invoice status
    if invoice.total == Decimal("0.00"):
        invoice.status = Invoice.Status.CANCELLED
    elif invoice.amount_paid >= invoice.total:
        invoice.status = Invoice.Status.PAID
    elif invoice.amount_paid > Decimal("0.00"):
        invoice.status = Invoice.Status.PARTIALLY_PAID
    else:
        invoice.status = Invoice.Status.ISSUED

    invoice.save(update_fields=["subtotal", "tax", "total", "amount_paid", "status"])

    # Create Credit Note document
    credit_note = CreditNote.objects.create(
        business=business,
        invoice=invoice,
        customer=invoice.customer,
        number=credit_note_number,
        reason=reason,
        subtotal=subtotal,
        tax=tax,
        total=total,
        refund_method=refund_method,
        refund_amount=refund_amount,
        created_by=user,
    )

    for line in validated_lines:
        CreditNoteItem.objects.create(
            credit_note=credit_note,
            product=line["product"],
            quantity=line["quantity"],
            unit_price=line["unit_price"],
            line_total=line["line_total"],
        )

    return credit_note
