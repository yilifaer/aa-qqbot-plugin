"""Django admin (docs/SPEC.md section 7).

Day-to-day management happens on the front-end manage pages; the admin is a
fallback for superusers. Bindings and the audit log are view-only here
because every binding change must go through ``qqbot.core`` (locks, events
for the bot, audit records). The one exception: deleting an AA user here
also deletes their binding (the cascade), and qqbot's ``pre_delete``
receiver records it and tells the bot. Group and settings saves emit the
same events and audit records as the manage pages.

Django 后台（见 docs/SPEC.md 第 7 节）。

日常管理在前台管理页面上进行；后台只是给超级用户的备用入口。绑定和审计日志
在这里只能查看，因为每次修改绑定都必须经过 ``qqbot.core``（加锁、给机器人
发事件、写审计记录）。唯一的例外：在后台删除 AA 用户时，他的绑定会跟着删除
（级联删除），qqbot 的 ``pre_delete`` 接收器会记录下来并通知机器人。在这里
保存群和设置时，会和管理页面一样发出事件、写审计记录。
"""

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils.translation import pgettext_lazy

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
        # 在多对多字段（required_groups）保存之后运行，处于后台的事务之内。
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

    @admin.display(description=pgettext_lazy("qqbot", "Main character"))
    def main_character(self, obj):
        profile = getattr(obj.user, "profile", None)
        char = getattr(profile, "main_character", None)
        return char.character_name if char else "—"

    def has_delete_permission(self, request, obj=None):
        # Deleting an AA user cascades to their binding, and Django's admin
        # asks this for every cascaded object (obj is set). Allow it: the
        # User admin's own permission (auth.delete_user) guards the deletion,
        # and qqbot's pre_delete receiver writes USER_DELETED and a recheck
        # event. obj=None stays False: no "delete selected" action here.
        # 删除 AA 用户时会级联删除他的绑定，Django 后台会对每个被级联删除的
        # 对象调用这里（obj 不为空）。这时放行：删除本身由用户后台自己的权限
        # （auth.delete_user）把关，qqbot 的 pre_delete 接收器会写 USER_DELETED
        # 审计并发复查事件。obj 为空时仍返回 False：这里没有「删除所选」。
        return obj is not None

    def delete_view(self, request, object_id, extra_context=None):
        # A binding itself is only removed through qqbot.core.
        # 绑定本身只能通过 qqbot.core 删除。
        raise PermissionDenied

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = {**(extra_context or {}), "show_delete": False}
        return super().change_view(request, object_id, form_url, extra_context)


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
            # 每个群名片都可能变化：在后台重新计算所有绑定。
            from .tasks import queue_reconcile

            # robust: the settings are saved by then; a failure is logged.
            # robust：这时设置已经保存；出错只记日志。
            transaction.on_commit(queue_reconcile, robust=True)
