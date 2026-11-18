from django.contrib import admin

from .models import (
    Business,
    CreditNote,
    CreditNoteItem,
    Customer,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentReminder,
    Product,
    StaffProfile,
)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


class CreditNoteItemInline(admin.TabularInline):
    model = CreditNoteItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "business", "customer", "status", "total", "amount_paid", "due_date")
    list_filter = ("status", "business")
    inlines = [InvoiceItemInline]


@admin.register(CreditNote)
class CreditNoteAdmin(admin.ModelAdmin):
    list_display = ("number", "business", "invoice", "customer", "total", "refund_method", "refund_amount", "created_at")
    list_filter = ("refund_method", "business")
    inlines = [CreditNoteItemInline]


@admin.register(PaymentReminder)
class PaymentReminderAdmin(admin.ModelAdmin):
    list_display = ("invoice", "customer", "phone", "status", "sent_at")
    list_filter = ("status",)
    search_fields = ("phone", "invoice__number", "customer__name")


admin.site.register(Business)
admin.site.register(StaffProfile)
admin.site.register(Customer)
admin.site.register(Product)
admin.site.register(Payment)
