"""Data model for aa-qqbot.

See DESIGN.md (Chinese, owner-facing) and docs/SPEC.md (implementation spec).
"""

import re

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

QQ_RE = re.compile(r"^[1-9][0-9]{4,10}$")

# Full-width digits -> ASCII digits, used when normalizing user input.
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_qq(value) -> str:
    """Normalize a QQ / group number to a canonical ASCII digit string.

    Accepts ints and strings; strips whitespace and converts full-width
    digits. Returns ``""`` when the value is not a valid 5-11 digit number
    (leading zero not allowed).
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return ""
    value = "".join(value.split()).translate(_FULLWIDTH_DIGITS)
    return value if QQ_RE.match(value) else ""


def validate_qq(value):
    if not normalize_qq(value) or normalize_qq(value) != value:
        raise ValidationError("请输入 5–11 位数字（不能以 0 开头）。")


class General(models.Model):
    """Unmanaged model that only carries this app's permissions."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "QQ 绑定 - 成员：可以绑定自己的 QQ"),
            ("manage", "QQ 绑定 - 管理员：可以在前台管理 QQ 群与绑定"),
        )


class Config(models.Model):
    """Singleton with settings editable by QQ managers on the front end."""

    DEFAULT_RULES = (
        "加入联盟 QQ 群即表示你同意遵守群规，入群后请认真阅读群公告。\n"
        "· 禁止传播涉黄、涉赌、涉毒的内容，包括讨论、链接、图片和视频；\n"
        "· 禁止人身攻击、种族歧视、造谣诽谤，以及低俗、暴力或其他负面内容；\n"
        "· 禁止讨论政治话题，避免触及敏感内容。\n"
        "让我们一起维护健康、友好的交流环境。"
    )
    DEFAULT_CARD_FORMAT = "[{corp_ticker}] {character_name} - {nickname}"

    rules_text = models.TextField("入群须知", default=DEFAULT_RULES, blank=True)
    card_format = models.CharField(
        "群名片格式",
        max_length=100,
        default=DEFAULT_CARD_FORMAT,
        help_text=(
            "可用占位符：{corp_ticker} 军团简称、{alliance_ticker} 联盟简称、"
            "{character_name} 主角色名、{nickname} 昵称"
        ),
    )
    code_ttl_minutes = models.PositiveSmallIntegerField(
        "验证码有效期（分钟）",
        default=10,
        validators=[MinValueValidator(5), MaxValueValidator(60)],
    )
    roster_max_age_days = models.PositiveSmallIntegerField(
        "群成员名单有效期（天）",
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text="机器人超过这么多天没有上报某个群的成员名单，该群就不能再用于「老成员免验证」。",
    )
    rebind_cooldown_hours = models.PositiveSmallIntegerField(
        "换绑冷却（小时）",
        default=24,
        validators=[MinValueValidator(0), MaxValueValidator(720)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = "设置"
        verbose_name_plural = "设置"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "Config":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "QQ 绑定设置"


class QQGroup(models.Model):
    """A QQ group managed by the bot and shown to eligible members."""

    class Kind(models.TextChoices):
        FIXED = "fixed", "固定群"
        ROLE = "role", "身份组小群"

    name = models.CharField("群名称", max_length=64)
    group_id = models.CharField("群号", max_length=11, unique=True, validators=[validate_qq])
    kind = models.CharField("类型", max_length=8, choices=Kind.choices, default=Kind.FIXED)
    required_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="+",
        verbose_name="需要的 AA 组",
        help_text="只对身份组小群有效：属于其中任意一个组即可。",
    )
    description = models.CharField("说明", max_length=200, blank=True)
    sort_order = models.PositiveIntegerField("排序", default=100)
    is_active = models.BooleanField("启用", default=True)
    # Set when the bot reports a complete member list for this group.
    last_roster_at = models.DateTimeField("最近一次名单上报", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        ordering = ("kind", "sort_order", "name")
        verbose_name = "QQ 群"
        verbose_name_plural = "QQ 群"

    def __str__(self):
        return f"{self.name} ({self.group_id})"


class Binding(models.Model):
    """The one QQ number bound to an AA user (decision #6: one per account).

    ``verified`` bindings own their QQ exclusively (DB-level partial unique
    constraint). ``trusted`` bindings ("老成员免验证") may collide with another
    trusted binding for the same QQ, which is a conflict for managers to
    resolve. Pending (not yet verified) submissions live in :class:`BindCode`,
    not here.
    """

    class Status(models.TextChoices):
        VERIFIED = "verified", "已验证"
        TRUSTED = "trusted", "老成员免验证"

    class VerifiedVia(models.TextChoices):
        NONE = "", "—"
        CODE = "code", "验证码"
        MANAGER = "manager", "管理员确认"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="qqbot_binding")
    qq = models.CharField("QQ 号", max_length=11, db_index=True, validators=[validate_qq])
    nickname = models.CharField("昵称", max_length=32)
    status = models.CharField("状态", max_length=10, choices=Status.choices)
    verified_via = models.CharField(
        "验证方式", max_length=10, choices=VerifiedVia.choices, blank=True, default=""
    )
    verified_at = models.DateTimeField("验证时间", null=True, blank=True)
    card_override = models.CharField(
        "管理员指定的群名片", max_length=60, blank=True, help_text="留空则按设置里的格式自动生成。"
    )
    # Last time the member changed their QQ (for the rebind cooldown).
    qq_changed_at = models.DateTimeField(null=True, blank=True)
    # Hash of the last computed per-group decisions + card; used by
    # reconciliation to emit events only when something actually changed.
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = "QQ 绑定"
        verbose_name_plural = "QQ 绑定"
        constraints = [
            models.UniqueConstraint(
                fields=["qq"],
                condition=Q(status="verified"),
                name="qqbot_unique_verified_qq",
            )
        ]

    def __str__(self):
        return f"{self.user.username} - {self.qq}"


class BindCode(models.Model):
    """One-time verification code for binding ``qq`` to ``user``.

    Only an HMAC of the code is stored. A user has at most one live code.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="qqbot_codes")
    qq = models.CharField(max_length=11, db_index=True, validators=[validate_qq])
    nickname = models.CharField(max_length=32)
    code_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        verbose_name = "验证码"
        verbose_name_plural = "验证码"

    def __str__(self):
        return f"code for {self.user.username} / {self.qq}"


class RosterEntry(models.Model):
    """A QQ number seen in the latest complete member list of a group."""

    group = models.ForeignKey(QQGroup, on_delete=models.CASCADE, related_name="roster")
    qq = models.CharField(max_length=11, db_index=True)
    seen_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["group", "qq"], name="qqbot_unique_roster_entry")
        ]


class Event(models.Model):
    """Outbox of changes the bot polls (``events`` API, cursor = id)."""

    class Kind(models.TextChoices):
        RECHECK = "recheck", "复查该 QQ"
        CARD = "card", "群名片变化"
        RECHECK_ALL = "recheck_all", "全部复查"
        GROUPS = "groups", "群配置变化"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    qq = models.CharField(max_length=11, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()
        ordering = ("id",)


class AuditLog(models.Model):
    """Who did what to which QQ, and when."""

    class Action(models.TextChoices):
        BIND = "bind", "绑定"
        VERIFY = "verify", "验证成功"
        CLAIM_FAILED = "claim_failed", "验证失败"
        CONFIRM = "confirm", "管理员确认"
        CONFLICT = "conflict", "冲突"
        CONFLICT_RESOLVED = "conflict_resolved", "冲突解决"
        REBIND = "rebind", "换绑"
        UNBIND = "unbind", "解绑"
        FORCE_UNBIND = "force_unbind", "强制解绑"
        NICKNAME = "nickname", "修改昵称"
        CARD = "card", "修改群名片"
        CODE = "code", "生成验证码"
        GROUP = "group", "修改群配置"
        CONFIG = "config", "修改设置"
        USER_DELETED = "user_deleted", "账号删除"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=24, choices=Action.choices)
    qq = models.CharField(max_length=11, blank=True, db_index=True)
    target_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    target_name = models.CharField(max_length=150, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ("-id",)
        verbose_name = "操作记录"
        verbose_name_plural = "操作记录"
