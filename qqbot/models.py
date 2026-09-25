"""Data model for aa-qqbot.

See DESIGN.md (Chinese, owner-facing) and docs/SPEC.md (implementation spec).

aa-qqbot 的数据模型。

详见 DESIGN.md（中文，写给站长看的）和 docs/SPEC.md（实现规格）。
"""

import re

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext, gettext_lazy as _, pgettext_lazy

QQ_RE = re.compile(r"^[1-9][0-9]{4,10}$")

# Full-width digits -> ASCII digits, used when normalizing user input.
# 全角数字 -> 半角 ASCII 数字，规范化用户输入时使用。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_qq(value) -> str:
    """Normalize a QQ / group number to a canonical ASCII digit string.

    Accepts ints and strings; strips whitespace and converts full-width
    digits. Returns ``""`` when the value is not a valid 5-11 digit number
    (leading zero not allowed).

    把 QQ 号 / 群号规范成标准的 ASCII 数字字符串。

    接受整数和字符串；会去掉空白，并把全角数字转成半角。如果不是合法的
    5–11 位数字（不能以 0 开头），返回 ``""``。
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
        raise ValidationError(gettext("Enter 5–11 digits (not starting with 0)."))


class General(models.Model):
    """Unmanaged model that only carries this app's permissions.

    不建表的模型（managed = False），只用来挂本插件的权限。
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            # English first so IT can search the admin permission picker in
            # English; the Chinese part keeps the names the Chinese docs use.
            # 英文在前，方便 IT 在后台权限选择框里用英文搜索；
            # 中文部分保持中文文档里用的名字不变。
            ("basic_access", "QQ binding: member, can bind own QQ / QQ 绑定 - 成员：可以绑定自己的 QQ"),
            (
                "manage",
                "QQ binding: manager, can manage QQ groups and bindings"
                " / QQ 绑定 - 管理员：可以在前台管理 QQ 群与绑定",
            ),
        )


class Config(models.Model):
    """Singleton with settings editable by QQ managers on the front end.

    单例设置（只有一行），QQ 管理员可以在前台修改。
    """

    DEFAULT_RULES = (
        "加入联盟 QQ 群即表示你同意遵守群规，入群后请认真阅读群公告。\n"
        "· 禁止传播涉黄、涉赌、涉毒的内容，包括讨论、链接、图片和视频；\n"
        "· 禁止人身攻击、种族歧视、造谣诽谤，以及低俗、暴力或其他负面内容；\n"
        "· 禁止讨论政治话题，避免触及敏感内容。\n"
        "让我们一起维护健康、友好的交流环境。"
    )
    DEFAULT_CARD_FORMAT = "[{corp_ticker}] {character_name} - {nickname}"

    rules_text = models.TextField(_("Group rules"), default=DEFAULT_RULES, blank=True)
    card_format = models.CharField(
        _("Group nickname format"),
        max_length=100,
        default=DEFAULT_CARD_FORMAT,
        help_text=_(
            "Placeholders: {corp_ticker} corporation ticker, {alliance_ticker} alliance ticker, "
            "{character_name} main character name, {nickname} nickname"
        ),
    )
    code_ttl_minutes = models.PositiveSmallIntegerField(
        _("Verification code lifetime (minutes)"),
        default=10,
        validators=[MinValueValidator(5), MaxValueValidator(60)],
    )
    roster_max_age_days = models.PositiveSmallIntegerField(
        _("Member list max age (days)"),
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text=_(
            "If the bot has not reported a group's member list for this many days, "
            "that group no longer counts for \"Trusted (already in group)\"."
        ),
    )
    trusted_window_days = models.PositiveSmallIntegerField(
        _("Trusted binding window (days)"),
        default=30,
        validators=[MinValueValidator(0), MaxValueValidator(365)],
        help_text=_(
            "Members already in a group can bind without a verification code only during "
            "this many days after the group was added here. After that, everyone uses a "
            "code. 0 turns this off."
        ),
    )
    rebind_cooldown_hours = models.PositiveSmallIntegerField(
        _("Change QQ cooldown (hours)"),
        default=24,
        validators=[MinValueValidator(0), MaxValueValidator(720)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = _("Settings")
        verbose_name_plural = _("Settings")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "Config":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return gettext("QQ binding settings")


class QQGroup(models.Model):
    """A QQ group managed by the bot and shown to eligible members.

    由机器人管理、展示给符合条件的成员的 QQ 群。
    """

    class Kind(models.TextChoices):
        FIXED = "fixed", _("Fixed group")
        ROLE = "role", _("Role group")

    name = models.CharField(_("Group name"), max_length=64)
    group_id = models.CharField(_("Group number"), max_length=11, unique=True, validators=[validate_qq])
    kind = models.CharField(_("Type"), max_length=8, choices=Kind.choices, default=Kind.FIXED)
    required_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="+",
        verbose_name=_("Required AA groups"),
        help_text=_("Only for role groups: being in any one of these groups is enough."),
    )
    description = models.CharField(pgettext_lazy("qqbot", "Description"), max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(_("Sort order"), default=100)
    is_active = models.BooleanField(pgettext_lazy("qqbot", "Enabled"), default=True)
    # Set when the bot reports a complete member list for this group.
    # 机器人上报该群完整的群成员名单时更新。
    last_roster_at = models.DateTimeField(_("Last member list report"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        ordering = ("kind", "sort_order", "name")
        verbose_name = _("QQ group")
        verbose_name_plural = _("QQ groups")

    def __str__(self):
        return f"{self.name} ({self.group_id})"


class Binding(models.Model):
    """The one QQ number bound to an AA user (decision #6: one per account).

    ``verified`` bindings own their QQ exclusively: ``verified_qq`` mirrors
    ``qq`` while verified and is NULL otherwise, and carries a plain unique
    index. (A conditional ``UniqueConstraint`` would need a partial index,
    which MySQL/MariaDB -- AllianceAuth's usual database -- do not support, so
    Django would silently skip it there.) ``trusted`` bindings ("老成员免验证") may collide with another
    trusted binding for the same QQ, which is a conflict for managers to
    resolve. Pending (not yet verified) submissions live in :class:`BindCode`,
    not here.

    AA 用户绑定的那一个 QQ 号（DECISIONS.md #6：每个账号只能绑一个）。

    ``verified``（已验证）的绑定独占这个 QQ：已验证时 ``verified_qq`` 与
    ``qq`` 相同，否则为 NULL，并且这一列带普通的唯一索引。（带条件的
    ``UniqueConstraint`` 需要部分索引，而 AllianceAuth 常用的 MySQL/MariaDB
    不支持部分索引，Django 在那里会悄悄跳过它。）``trusted``（老成员免验证）
    的绑定可能和另一个同 QQ 的 trusted 绑定撞在一起，这算冲突，由管理员处理。
    还没验证的提交放在 :class:`BindCode` 里，不在这里。
    """

    class Status(models.TextChoices):
        VERIFIED = "verified", _("Verified")
        TRUSTED = "trusted", _("Trusted (already in group)")

    class VerifiedVia(models.TextChoices):
        NONE = "", "—"
        CODE = "code", _("Verification code")
        MANAGER = "manager", _("Confirmed by manager")

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="qqbot_binding")
    qq = models.CharField(_("QQ number"), max_length=11, db_index=True, validators=[validate_qq])
    nickname = models.CharField(_("Nickname"), max_length=32)
    status = models.CharField(_("Status"), max_length=10, choices=Status.choices)
    verified_via = models.CharField(
        _("Verified via"), max_length=10, choices=VerifiedVia.choices, blank=True, default=""
    )
    verified_at = models.DateTimeField(_("Verified at"), null=True, blank=True)
    card_override = models.CharField(
        _("Group nickname set by manager"),
        max_length=60,
        blank=True,
        help_text=_("Leave empty to generate it from the format in Settings."),
    )
    # Last time the member changed their QQ (for the rebind cooldown).
    # 成员最近一次更换 QQ 的时间（用于换绑冷却）。
    qq_changed_at = models.DateTimeField(null=True, blank=True)
    # Hash of the last computed per-group decisions + card; used by
    # reconciliation to emit events only when something actually changed.
    # 上次算出的各群判断结果 + 群名片的哈希；对账时用它来判断，
    # 只有真的有变化才发事件。
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    # == qq while status is verified, NULL otherwise (kept in sync by save()).
    # Unique, so a verified QQ has one owner on every database backend.
    # 状态为已验证时等于 qq，否则为 NULL（由 save() 保持同步）。
    # 这一列唯一，所以不管用哪种数据库，一个已验证的 QQ 只会属于一个人。
    verified_qq = models.CharField(
        max_length=11, null=True, blank=True, unique=True, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        verbose_name = _("QQ binding")
        verbose_name_plural = _("QQ bindings")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(status="verified", verified_qq=F("qq"))
                    | (~Q(status="verified") & Q(verified_qq__isnull=True))
                ),
                name="qqbot_verified_qq_in_sync",
            )
        ]

    def save(self, *args, **kwargs):
        self.verified_qq = self.qq if self.status == self.Status.VERIFIED else None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"qq", "status"} & set(update_fields):
            kwargs["update_fields"] = {*update_fields, "verified_qq"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.qq}"


class Lock(models.Model):
    """Rows used as mutexes (``SELECT ... FOR UPDATE``) by ``qqbot.core.locks``.

    Users and QQ numbers are hashed onto a fixed set of rows that the
    migration creates, so taking a lock never inserts anything. See
    ``core/locks.py`` for the lock order.

    ``qqbot.core.locks`` 当作互斥锁用的行（``SELECT ... FOR UPDATE``）。

    用户和 QQ 号会被哈希到迁移预先建好的一组固定行上，所以加锁时不会插入
    任何数据。加锁顺序见 ``core/locks.py``。
    """

    id = models.PositiveIntegerField(primary_key=True)

    class Meta:
        default_permissions = ()


class BindCode(models.Model):
    """One-time verification code for binding ``qq`` to ``user``.

    Only an HMAC of the code is stored. A user has at most one live code.

    把 ``qq`` 绑定到 ``user`` 用的一次性验证码。

    数据库里只存验证码的 HMAC。每个用户最多只有一个有效的验证码。
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
        verbose_name = _("Verification code")
        verbose_name_plural = _("Verification codes")

    def __str__(self):
        return f"code for {self.user.username} / {self.qq}"


class RosterEntry(models.Model):
    """A QQ number seen in the latest complete member list of a group.

    在某个群最新一份完整的群成员名单里出现过的 QQ 号。
    """

    group = models.ForeignKey(QQGroup, on_delete=models.CASCADE, related_name="roster")
    qq = models.CharField(max_length=11, db_index=True)
    seen_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["group", "qq"], name="qqbot_unique_roster_entry")
        ]


class Event(models.Model):
    """Outbox of changes the bot polls (``events`` API, cursor = id).

    变更发件箱，机器人会来轮询（``events`` 接口，游标 = id）。
    """

    class Kind(models.TextChoices):
        RECHECK = "recheck", _("Recheck QQ")
        CARD = "card", _("Group nickname changed")
        RECHECK_ALL = "recheck_all", _("Recheck all")
        GROUPS = "groups", _("Group settings changed")

    kind = models.CharField(max_length=16, choices=Kind.choices)
    qq = models.CharField(max_length=11, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()
        ordering = ("id",)


class AuditLog(models.Model):
    """Who did what to which QQ, and when.

    操作记录：谁在什么时候对哪个 QQ 做了什么。
    """

    class Action(models.TextChoices):
        BIND = "bind", _("Bind")
        VERIFY = "verify", _("Verification succeeded")
        CLAIM_FAILED = "claim_failed", _("Verification failed")
        CONFIRM = "confirm", _("Confirmed by manager")
        CONFLICT = "conflict", _("Conflict")
        CONFLICT_RESOLVED = "conflict_resolved", _("Conflict resolved")
        REBIND = "rebind", _("Change QQ")
        UNBIND = "unbind", _("Unbind")
        FORCE_UNBIND = "force_unbind", _("Force unbind")
        NICKNAME = "nickname", _("Change nickname")
        CARD = "card", _("Change group nickname")
        CODE = "code", _("Generate verification code")
        GROUP = "group", _("Change group settings")
        CONFIG = "config", _("Change settings")
        USER_DELETED = "user_deleted", _("Account deleted")

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
        verbose_name = _("Audit log entry")
        verbose_name_plural = _("Audit log")
