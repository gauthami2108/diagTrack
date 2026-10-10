"""
Administration screens (the Masters). Reached from Administration in the side menu.
Only administrators can open them.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group

from .models import (Category, CategoryField, Centre, Department, Equipment, EscalationRule,
                     OutboundMessage, SlaPolicy, TicketHistory, User, Vendor, VendorCall)

admin.site.site_header = "DiagTrack administration"
admin.site.site_title = "DiagTrack administration"
admin.site.index_title = "Masters and rules"
admin.site.site_url = "/"
admin.site.unregister(Group)


@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "city", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")

    def get_readonly_fields(self, request, obj=None):
        # the code is part of every ticket number, so it is fixed once tickets exist
        return ("code",) if obj and obj.tickets.exists() else ()


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "head", "assignment_mode", "handles_equipment", "active")
    list_filter = ("active",)
    autocomplete_fields = ("head",)


class CategoryFieldInline(admin.TabularInline):
    model = CategoryField
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "department", "default_priority", "asks_equipment", "active")
    list_filter = ("department", "active")
    search_fields = ("name",)
    inlines = [CategoryFieldInline]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active", "centres", "departments")
    search_fields = ("username", "first_name", "last_name", "email", "mobile")
    filter_horizontal = ("centres", "departments")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Person", {"fields": ("first_name", "last_name", "email", "mobile", "photo")}),
        ("What they do", {"fields": ("role", "centres", "departments")}),
        ("Login", {"fields": ("is_active",),
                   "description": "Untick Active when someone leaves. Never delete a person who has tickets."}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": (
            "username", "password1", "password2", "first_name", "last_name", "email", "mobile",
            "role", "centres", "departments")}),
    )

    def save_model(self, request, obj, form, change):
        # administrators may open these screens; nobody else
        obj.is_staff = obj.is_superuser = obj.role == User.ADMIN
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone", "email")
    search_fields = ("name",)


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "centre", "make_and_model", "serial_no", "vendor", "amc_expiry", "active")
    list_filter = ("centre", "active")
    search_fields = ("name", "serial_no", "make", "model")

    @admin.display(description="Make and model", ordering="make")
    def make_and_model(self, e):
        return f"{e.make} {e.model}".strip()


@admin.register(SlaPolicy)
class SlaPolicyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "department", "priority", "response_minutes", "resolution_minutes")
    list_editable = ("response_minutes", "resolution_minutes")
    list_filter = ("department", "priority")


@admin.register(EscalationRule)
class EscalationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "basis", "percent", "priorities", "who_is_alerted", "active")
    list_editable = ("active",)

    @admin.display(description="Who is alerted")
    def who_is_alerted(self, rule):
        who = [label for flag, label in ((rule.notify_assignee, "Assigned agent"), (rule.notify_dept_head, "Department head"),
                                         (rule.notify_centre_manager, "Centre manager"),
                                         (rule.notify_area_manager, "Area manager")) if flag]
        return ", ".join(who) or "Nobody"


@admin.register(OutboundMessage)
class OutboundMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "channel", "to", "subject", "status", "error")
    list_filter = ("channel", "status")
    readonly_fields = [f.name for f in OutboundMessage._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(TicketHistory)
class TicketHistoryAdmin(admin.ModelAdmin):
    """Read-only audit trail."""
    list_display = ("created_at", "ticket", "actor", "text", "old", "new")
    search_fields = ("ticket__number", "text")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in TicketHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VendorCall)
class VendorCallAdmin(admin.ModelAdmin):
    """Read-only list of vendor calls. Calls are logged from the ticket page."""
    list_display = ("called_at", "vendor", "ticket", "reference", "engineer", "attended_at")
    list_filter = ("vendor",)
    search_fields = ("ticket__number", "reference", "engineer")
    readonly_fields = [f.name for f in VendorCall._meta.fields]

    def has_add_permission(self, request):
        return False
