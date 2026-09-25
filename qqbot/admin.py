"""Django admin (docs/SPEC.md section 7).

Day-to-day management happens on the front-end manage pages; the admin is a
fallback for superusers. Bindings and the audit log are view-only here
because every binding change must go through ``qqbot.core`` (locks, events
for the bot, audit records). Group and settings saves emit the same events
and audit records as the manage pages.
"""

from django.contrib import admin
from django.db import transaction

from .core import audit, events
from .models import AuditLog, Binding, Config, QQGroup
from .views.manage_forms import ConfigForm, QQGroupForm


def _group_detail(group: QQGroup) -> dict:
    return {
        "group_id": group.group_id,
        "name": group.name,
        "kind": group.kind,
        "is_active": group.is_active,
        "required_groups": sorted(group.required_groups.values_list("name", flat=True)),
    }


@admin.register(QQGroup)
class QQGroupAdmin(admin.ModelAdmin):
    form = QQGroupForm
    list_display = ("name", "group_id", "kind", "is_active", "sort_order", "last_roster_at")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "group_id")
    ordering = ("kind", "sort_order", "name")
    readonly_fields = ("last_roster_at", "created_at", "updated_at")
    fields = (
        "name",
        "group_id",
        "kind",
        "required_groups",
        "description",
        "sort_order",
        "is_active",
        "last_roster_at",
        "created_at",
        "updated_at",
    )

    def save_related(self, request, form, formsets, change):
        # Runs after the m2m (required_groups) is saved, inside the admin's
        # transaction.
        super().save_related(request, form, formsets, change)
        group = form.instance
        events.emit_groups_changed()
        audit.log(
            AuditLog.Action.GROUP,
            actor=request.user,
            op="update" if change else "create",
            via="admin",
            **_group_detail(group),
        )

    def _deleted(self, request, detail):
        events.emit_groups_changed()
        audit.log(AuditLog.Action.GROUP, actor=request.user, op="delete", via="admin", **detail)

    def delete_model(self, request, obj):
        with transaction.atomic():
            detail = _group_detail(obj)
            super().delete_model(request, obj)
            self._deleted(request, detail)

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            for group in list(queryset):
                detail = _group_detail(group)
                group.delete()
                self._deleted(request, detail)


class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Binding)
class BindingAdmin(_ReadOnlyAdmin):
    list_display = (
        "user",
        "main_character",
        "qq",
        "nickname",
        "status",
        "verified_via",
        "verified_at",
        "updated_at",
    )
    list_filter = ("status", "verified_via")
    list_select_related = ("user", "user__profile__main_character")
    search_fields = (
        "user__username",
        "user__profile__main_character__character_name",
        "qq",
        "nickname",
    )
    ordering = ("-updated_at",)
    exclude = ("fingerprint",)

    @admin.display(description="主角色")
    def main_character(self, obj):
        profile = getattr(obj.user, "profile", None)
        char = getattr(profile, "main_character", None)
        return char.character_name if char else "—"


@admin.register(AuditLog)
class AuditLogAdmin(_ReadOnlyAdmin):
    list_display = ("created_at", "action", "actor_name", "qq", "target_name")
    list_filter = ("action",)
    search_fields = ("qq", "actor_name", "target_name")
    ordering = ("-id",)
    date_hierarchy = "created_at"


@admin.register(Config)
class ConfigAdmin(admin.ModelAdmin):
    form = ConfigForm
    list_display = ("__str__", "card_format", "code_ttl_minutes", "updated_at")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not Config.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        changed = list(form.changed_data)
        audit.log(AuditLog.Action.CONFIG, actor=request.user, changed=changed, via="admin")
        if "card_format" in changed:
            # Every card may change: recompute all bindings in the background.
            from .tasks import queue_reconcile

            transaction.on_commit(queue_reconcile)
