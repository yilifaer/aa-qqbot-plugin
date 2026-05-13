from django.contrib import admin
from .models import QQBinding


@admin.register(QQBinding)
class QQBindingAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "main_character_name",
        "corp_ticket",
        "qq",
        "nickname",
        "created_at",
    )
    search_fields = (
        "user__username",
        "main_character_name",
        "qq",
        "nickname",
        "corp_ticket",
    )
    list_filter = ("created_at",)
    readonly_fields = ("created_at", "updated_at")
