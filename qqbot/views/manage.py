"""QQ manager pages (docs/SPEC.md section 6, DESIGN.md 4.3).

Every view needs a login and ``qqbot.manage``; changes are POST-only.
Binding changes go through ``qqbot.core.bindings``; group and settings
saves emit events and audit records through ``qqbot.core``.
Managers see full QQ numbers.

QQ 管理员页面（docs/SPEC.md 第 6 节，DESIGN.md 4.3）。

每个视图都要求已登录并拥有 ``qqbot.manage`` 权限；修改操作只接受 POST。
绑定的修改统一走 ``qqbot.core.bindings``；保存群和设置时，
通过 ``qqbot.core`` 发出事件并写审计记录。
管理员可以看到完整的 QQ 号。
"""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import gettext, pgettext, pgettext_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from allianceauth.services.hooks import get_extension_logger

from .. import tasks
from ..core import audit, bindings, cards, eligibility, events
from ..core.roster import fresh_roster_cutoff, unbound_roster
from ..i18n import ui_language_view
from ..models import AuditLog, Binding, Config, QQGroup, RosterEntry
from .manage_forms import (
    AuditFilterForm,
    BindingFilterForm,
    CardOverrideForm,
    ConfigForm,
    QQGroupForm,
)

logger = get_extension_logger(__name__)

MANAGE = "qqbot.manage"
PAGE_SIZE = 50
# "Needs attention" lists bindings made without a code in this many days
# (decision #22).
# 「待处理」页列出最近这么多天内的免验证绑定（决定 #22）。
RECENT_TRUSTED_DAYS = 7

REASON_LABELS = {
    eligibility.OK: _("Eligible"),
    eligibility.NOT_BOUND: _("Not bound"),
    eligibility.PENDING_VERIFY: pgettext_lazy("qqbot", "Pending"),
    eligibility.CONFLICT: _("Conflict"),
    eligibility.USER_INACTIVE: _("Account disabled"),
    eligibility.NO_MAIN: _("No main character"),
    eligibility.NO_ACCESS: _("No member access"),
    eligibility.GROUP_ROLE_MISSING: _("Not in a required group"),
    eligibility.GROUP_MISCONFIGURED: _("Group misconfigured"),
}
DECISION_LABELS = {
    eligibility.ALLOW: _("Allow"),
    eligibility.DENY: _("Deny"),
    eligibility.REVIEW: _("Needs manual review"),
}
DECISION_BADGES = {
    eligibility.ALLOW: "text-bg-success",
    eligibility.DENY: "text-bg-danger",
    eligibility.REVIEW: "text-bg-warning",
}

# Where a POST action may send the manager back to (no free-form URLs).
# POST 操作完成后可以把管理员送回哪些页面（只能是这几个，不接受任意 URL）。
BACK_TARGETS = {"pending": "qqbot:manage_pending", "bindings": "qqbot:manage_bindings"}

BINDING_RELATED = ("user", "user__profile", "user__profile__main_character")


# --------------------------------------------------------------------------
# helpers
# 辅助函数
# --------------------------------------------------------------------------


def _conflict_qqs() -> list[str]:
    return [qq for qq, _rows in bindings.conflicts()]


def _fresh_unbound():
    """Unbound roster entries of active groups whose roster is fresh.

    已启用且群成员名单还在有效期内的群里，尚未绑定的名单条目。
    """
    return unbound_roster().filter(
        group__last_roster_at__isnull=False,
        group__last_roster_at__gte=fresh_roster_cutoff(),
    )


def _recent_trusted(now=None) -> list[Binding]:
    """Trusted bindings made (or moved to their QQ) in the last
    ``RECENT_TRUSTED_DAYS`` days, newest first, for managers to review.

    Only claims nobody else shares: conflicts are listed separately, and a
    trusted claim on a QQ someone verified has no effect.

    最近 ``RECENT_TRUSTED_DAYS`` 天内做的（或换绑到这个 QQ 的）免验证绑定，
    按时间倒序，给管理员复核。

    只列没有别人认领同一个 QQ 的：冲突另外列出；别人已经验证过的 QQ，
    免验证认领本来就不生效。
    """
    now = now or timezone.now()
    rows = list(
        Binding.objects.filter(
            status=Binding.Status.TRUSTED,
            qq_changed_at__gte=now - timedelta(days=RECENT_TRUSTED_DAYS),
        )
        .select_related(*BINDING_RELATED)
        .order_by("-qq_changed_at", "-pk")
    )
    shared = set(
        Binding.objects.filter(qq__in={b.qq for b in rows})
        .order_by()
        .values("qq")
        .annotate(n=Count("pk"))
        .filter(n__gt=1)
        .values_list("qq", flat=True)
    )
    return [b for b in rows if b.qq not in shared]


def _summary() -> dict:
    conflict_count = len(_conflict_qqs())
    unbound_count = _fresh_unbound().order_by().values("qq").distinct().count()
    return {
        "bound": Binding.objects.count(),
        "verified": Binding.objects.filter(status=Binding.Status.VERIFIED).count(),
        "trusted": Binding.objects.filter(status=Binding.Status.TRUSTED).count(),
        "conflicts": conflict_count,
        "unbound": unbound_count,
        "pending": conflict_count + unbound_count,
    }


def _ctx(tab: str, **extra) -> dict:
    ctx = {"manage_tab": tab, "summary": _summary()}
    ctx.update(extra)
    return ctx


def _message(request, result) -> None:
    level = messages.SUCCESS if result.ok else messages.ERROR
    if result.ok and result.outcome == "unchanged":
        level = messages.INFO
    messages.add_message(
        request, level, result.message or (gettext("Done.") if result.ok else gettext("Failed."))
    )


def _render(
    request, template_name: str, context: dict, status: int | None = None
) -> TemplateResponse:
    """A page response, rendered after the view returns, in the request's
    language (so AA's menus stay in it); ``qqbot/base.html`` renders qqbot's
    blocks in qqbot's UI language. See ``qqbot.i18n``.

    页面响应，在视图返回之后按请求的语言渲染（这样 AA 的菜单保持用户的
    语言）；``qqbot/base.html`` 用 qqbot 的界面语言渲染 qqbot 自己的块。
    见 ``qqbot.i18n``。
    """
    return TemplateResponse(request, template_name, context, status=status)


def _back(request, default: str, **kwargs):
    target = BACK_TARGETS.get(request.POST.get("back", ""))
    if target:
        return redirect(target)
    return redirect(default, **kwargs)


def _page(request, qs):
    return Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))


def _querystring(request) -> str:
    """Current GET parameters without ``page`` (for pagination links).

    当前请求的 GET 参数，去掉 ``page``（用来拼分页链接）。
    """
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _decision_row(decision) -> dict:
    return {
        "decision": decision.decision,
        "decision_label": DECISION_LABELS.get(decision.decision, decision.decision),
        "badge": DECISION_BADGES.get(decision.decision, "text-bg-secondary"),
        "reason": decision.reason,
        "reason_label": REASON_LABELS.get(decision.reason, decision.reason),
        "card": decision.card,
    }


def _group_audit_detail(group: QQGroup) -> dict:
    return {
        "group_id": group.group_id,
        "name": group.name,
        "kind": group.kind,
        "required_groups": sorted(group.required_groups.values_list("name", flat=True)),
        "is_active": group.is_active,
    }


# --------------------------------------------------------------------------
# index / groups
# 首页 / 群管理
# --------------------------------------------------------------------------


@login_required
@permission_required(MANAGE, raise_exception=True)
@ui_language_view
def index(request):
    return redirect("qqbot:manage_bindings")


@login_required
@permission_required(MANAGE, raise_exception=True)
@ui_language_view
def groups(request):
    qs = (
        QQGroup.objects.annotate(roster_count=Count("roster"))
        .prefetch_related("required_groups")
        .order_by("-is_active", "kind", "sort_order", "name")
    )
    cutoff = fresh_roster_cutoff()
    rows = [
        {
            "group": g,
            "required": sorted(rg.name for rg in g.required_groups.all()),
            "roster_fresh": bool(g.last_roster_at and g.last_roster_at >= cutoff),
        }
        for g in qs
    ]
    return _render(request, "qqbot/manage/groups.html", _ctx("groups", rows=rows))


def _save_group(request, form, op: str):
    """Save the group form; returns the group or None (errors on the form).

    保存群表单；成功时返回群对象，失败时返回 None（错误信息加在表单上）。
    """
    changed = list(form.changed_data)
    try:
        with transaction.atomic():
            group = form.save()
            events.emit_groups_changed()
            audit.log(
                AuditLog.Action.GROUP,
                actor=request.user,
                op=op,
                changed=changed,
                **_group_audit_detail(group),
            )
    except IntegrityError:
        form.add_error(
            "group_id", gettext("This group number has already been added; don't add it twice.")
        )
        return None
    return group


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
@ui_language_view
def group_create(request):
    form = QQGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = _save_group(request, form, "create")
        if group is not None:
            messages.success(request, gettext('Added group "%(name)s".') % {"name": group.name})
            return redirect("qqbot:manage_groups")
    return _render(
        request, "qqbot/manage/group_form.html", _ctx("groups", form=form, group=None)
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
@ui_language_view
def group_edit(request, pk):
    group = get_object_or_404(QQGroup, pk=pk)
    form = QQGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        if not form.has_changed():
            messages.info(request, gettext("Nothing to change."))
            return redirect("qqbot:manage_groups")
        group = _save_group(request, form, "update")
        if group is not None:
            messages.success(request, gettext('Saved group "%(name)s".') % {"name": group.name})
            return redirect("qqbot:manage_groups")
    return _render(
        request, "qqbot/manage/group_form.html", _ctx("groups", form=form, group=group)
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
@ui_language_view
def group_delete(request, pk):
    group = get_object_or_404(QQGroup, pk=pk)
    if request.method == "POST":
        with transaction.atomic():
            detail = _group_audit_detail(group)
            name = group.name
            group.delete()
            events.emit_groups_changed()
            audit.log(AuditLog.Action.GROUP, actor=request.user, op="delete", **detail)
        messages.success(request, gettext('Deleted group "%(name)s".') % {"name": name})
        return redirect("qqbot:manage_groups")
    return _render(
        request,
        "qqbot/manage/group_delete.html",
        _ctx("groups", group=group, roster_count=RosterEntry.objects.filter(group=group).count()),
    )


# --------------------------------------------------------------------------
# bindings
# 绑定
# --------------------------------------------------------------------------


@login_required
@permission_required(MANAGE, raise_exception=True)
@ui_language_view
def binding_list(request):
    form = BindingFilterForm(request.GET or None)
    qs = Binding.objects.select_related(*BINDING_RELATED).order_by("user__username", "pk")
    conflict_qqs = set(_conflict_qqs())
    form.is_valid()  # a bad value in one field must not drop the others / 一个字段出错不能连累其他筛选条件
    filters = form.cleaned_data if form.is_bound else {}
    if filters:
        q = (filters.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(user__username__icontains=q)
                | Q(user__profile__main_character__character_name__icontains=q)
                | Q(user__profile__main_character__corporation_ticker__iexact=q)
                | Q(qq__icontains=q)
                | Q(nickname__icontains=q)
            )
        if filters.get("status"):
            qs = qs.filter(status=filters["status"])
        if filters.get("conflict"):
            qs = qs.filter(qq__in=conflict_qqs, status=Binding.Status.TRUSTED)
    page = _page(request, qs)

    rows = list(page.object_list)
    config = Config.get_solo()
    groups_ = eligibility.active_groups()
    decisions = eligibility.evaluate_many(groups_, [b.qq for b in rows], config=config)
    shadowed = set(
        Binding.objects.filter(
            qq__in=[b.qq for b in rows if b.status == Binding.Status.TRUSTED],
            status=Binding.Status.VERIFIED,
        ).values_list("qq", flat=True)
    )
    items = []
    for b in rows:
        per_group = decisions.get(b.qq, {})
        allowed = sum(1 for d in per_group.values() if d.decision == eligibility.ALLOW)
        items.append(
            {
                "binding": b,
                "main": getattr(getattr(b.user, "profile", None), "main_character", None),
                "card": cards.render_card(b, config),
                "conflict": b.qq in conflict_qqs and b.status == Binding.Status.TRUSTED,
                "shadowed": b.qq in shadowed,
                "allowed": allowed,
                "group_total": len(groups_),
            }
        )
    return _render(
        request,
        "qqbot/manage/bindings.html",
        _ctx("bindings", form=form, page=page, items=items, querystring=_querystring(request)),
    )


def _get_binding(pk) -> Binding:
    return get_object_or_404(Binding.objects.select_related(*BINDING_RELATED), pk=pk)


def _binding_context(binding: Binding, card_form=None) -> dict:
    config = Config.get_solo()
    groups_ = eligibility.active_groups()
    decisions = eligibility.evaluate_binding(binding, groups_, config=config)
    table = [{"group": g, **_decision_row(decisions[g.pk])} for g in groups_ if g.pk in decisions]
    others = list(
        Binding.objects.filter(qq=binding.qq)
        .exclude(pk=binding.pk)
        .select_related(*BINDING_RELATED)
        .order_by("pk")
    )
    history = list(
        AuditLog.objects.filter(Q(qq=binding.qq) | Q(target_user_id=binding.user_id)).order_by(
            "-id"
        )[:20]
    )
    card = cards.render_card(binding, config)
    other_verified = any(o.status == Binding.Status.VERIFIED for o in others)
    return _ctx(
        "bindings",
        binding=binding,
        main=getattr(getattr(binding.user, "profile", None), "main_character", None),
        table=table,
        card=card,
        card_bytes=cards.byte_length(card),
        auto_card=cards.preview_card(binding.user, binding.nickname, config),
        card_max=cards.CARD_MAX_BYTES,
        card_form=card_form or CardOverrideForm(initial={"card": binding.card_override}),
        others=others,
        other_verified=other_verified,
        # Trusted claims on a QQ nobody verified (DESIGN 5.4).
        # 同一个 QQ 被多个账号以老成员免验证方式认领，且没有人验证过它（DESIGN 5.4）。
        conflict=(
            binding.status == Binding.Status.TRUSTED and bool(others) and not other_verified
        ),
        history=[_audit_row(a) for a in history],
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
@ui_language_view
def binding_detail(request, pk):
    binding = _get_binding(pk)
    return _render(request, "qqbot/manage/binding_detail.html", _binding_context(binding))


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_POST
@ui_language_view
def binding_card(request, pk):
    binding = _get_binding(pk)
    if request.POST.get("action") == "clear":
        form = CardOverrideForm({"card": ""})
    else:
        form = CardOverrideForm(request.POST)
    if form.is_valid():
        result = bindings.set_card_override(binding, form.cleaned_data["card"], request.user)
        if result.ok:
            _message(request, result)
            return redirect("qqbot:manage_binding", pk=binding.pk)
        form.add_error("card", result.message)
    return _render(
        request,
        "qqbot/manage/binding_detail.html",
        _binding_context(binding, card_form=form),
        status=400,
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_POST
@ui_language_view
def binding_confirm(request, pk):
    binding = _get_binding(pk)
    # The QQ shown on the page: a member may have rebound (same pk) since.
    # 传入页面上显示的 QQ：成员可能在这期间换绑了（pk 不变）。
    result = bindings.confirm(binding, request.user, expected_qq=request.POST.get("qq", ""))
    _message(request, result)
    return _back(request, "qqbot:manage_binding", pk=binding.pk)


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
@ui_language_view
def binding_unbind(request, pk):
    binding = _get_binding(pk)
    if request.method == "POST":
        result = bindings.unbind(
            binding.user, actor=request.user, forced=True,
            expected_qq=request.POST.get("qq", ""),
        )
        _message(request, result)
        if result.outcome == "qq_changed":
            return redirect("qqbot:manage_binding", pk=binding.pk)
        return _back(request, "qqbot:manage_bindings")
    back = request.GET.get("back", "")
    return _render(
        request,
        "qqbot/manage/binding_unbind.html",
        _ctx(
            "bindings",
            binding=binding,
            main=getattr(getattr(binding.user, "profile", None), "main_character", None),
            back=back if back in BACK_TARGETS else "",
        ),
    )


# --------------------------------------------------------------------------
# pending
# 待处理
# --------------------------------------------------------------------------


@login_required
@permission_required(MANAGE, raise_exception=True)
@ui_language_view
def pending(request):
    config = Config.get_solo()
    conflict_rows = [
        {
            "qq": qq,
            "bindings": [
                {
                    "binding": b,
                    "main": getattr(getattr(b.user, "profile", None), "main_character", None),
                    "card": cards.render_card(b, config),
                }
                for b in rows
            ],
        }
        for qq, rows in bindings.conflicts()
    ]

    by_group: dict[int, dict] = {}
    for entry in _fresh_unbound():
        slot = by_group.setdefault(entry.group_id, {"group": entry.group, "qqs": []})
        slot["qqs"].append(entry.qq)
    recent = _recent_trusted()
    groups_by_qq: dict[str, list[QQGroup]] = {}
    for entry in (
        RosterEntry.objects.filter(qq__in={b.qq for b in recent}, group__is_active=True)
        .select_related("group")
        .order_by("group__kind", "group__sort_order", "group__name")
    ):
        groups_by_qq.setdefault(entry.qq, []).append(entry.group)
    recent_trusted = [
        {
            "binding": b,
            "main": getattr(getattr(b.user, "profile", None), "main_character", None),
            "card": cards.render_card(b, config),
            "groups": groups_by_qq.get(b.qq, []),
        }
        for b in recent
    ]

    cutoff = fresh_roster_cutoff(config=config)
    stale_groups = list(
        QQGroup.objects.filter(is_active=True)
        .filter(Q(last_roster_at__isnull=True) | Q(last_roster_at__lt=cutoff))
        .order_by("kind", "sort_order", "name")
    )
    return _render(
        request,
        "qqbot/manage/pending.html",
        _ctx(
            "pending",
            conflicts=conflict_rows,
            recent_trusted=recent_trusted,
            recent_trusted_days=RECENT_TRUSTED_DAYS,
            unbound_groups=list(by_group.values()),
            stale_groups=stale_groups,
            roster_max_age_days=config.roster_max_age_days,
        ),
    )


# --------------------------------------------------------------------------
# settings
# 设置
# --------------------------------------------------------------------------


_CONFIG_SHORT_FIELDS = ("card_format", "code_ttl_minutes", "roster_max_age_days",
                        "trusted_window_days", "rebind_cooldown_hours")


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
@ui_language_view
def settings_view(request):
    config = Config.get_solo()
    before = {f: getattr(config, f) for f in _CONFIG_SHORT_FIELDS}
    form = ConfigForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        changed = list(form.changed_data)
        if not changed:
            messages.info(request, gettext("Nothing to change."))
            return redirect("qqbot:manage_settings")
        with transaction.atomic():
            config = form.save()
            detail = {"changed": changed}
            for f in _CONFIG_SHORT_FIELDS:
                if f in changed:
                    detail[f] = {"old": before[f], "new": getattr(config, f)}
            audit.log(AuditLog.Action.CONFIG, actor=request.user, **detail)
            if "card_format" in changed:
                # Every card may change: recompute all bindings in the
                # background (inline if the broker is unreachable).
                # 所有群名片都可能变化：在后台重新计算全部绑定
                # （连不上 broker 时直接在当前请求里执行）。
                # robust: the settings are saved by then; a failure is logged.
                # robust：这时设置已经保存；出错只记日志。
                transaction.on_commit(tasks.queue_reconcile, robust=True)
        if "card_format" in changed:
            messages.success(
                request,
                gettext(
                    "Settings saved. The group nickname format changed; everyone's group "
                    "nickname will be recalculated in the background."
                ),
            )
        else:
            messages.success(request, gettext("Settings saved."))
        return redirect("qqbot:manage_settings")
    return _render(
        request,
        "qqbot/manage/settings.html",
        _ctx(
            "settings",
            form=form,
            placeholders=cards.PLACEHOLDERS,
            example_card=cards.preview_card(request.user, gettext("Nickname"), config),
        ),
    )


# --------------------------------------------------------------------------
# audit
# 审计日志
# --------------------------------------------------------------------------


# Display labels for the keys and well-known values in AuditLog.detail
# (translated lazily, per request language).
# AuditLog.detail 里的键名和常见取值对应的显示文字（按请求语言延迟翻译）。
DETAIL_KEY_LABELS = {
    "op": _("Action"),
    "changed": _("Changed"),
    "group_id": _("Group number"),
    "name": _("Group name"),
    "kind": _("Type"),
    "required_groups": _("Required AA groups"),
    "description": pgettext_lazy("qqbot", "Description"),
    "sort_order": _("Sort order"),
    "is_active": _("Enabled"),
    "status": _("Status"),
    "previous_status": _("Previous status"),
    "old": _("Old"),
    "new": _("New"),
    "old_qq": _("Previous QQ"),
    "others": _("Other claiming accounts"),
    "reason": _("Reason"),
    "winner": _("Winning account"),
    "via": _("Method"),
    "expires_at": _("Expires at"),
    "applicant_qq": _("Applicant QQ"),
    "rules_text": _("Group rules"),
    "card_format": _("Group nickname format"),
    "code_ttl_minutes": _("Verification code lifetime (minutes)"),
    "roster_max_age_days": _("Member list max age (days)"),
    "trusted_window_days": _("Trusted binding window (days)"),
    "rebind_cooldown_hours": _("Change QQ cooldown (hours)"),
}
DETAIL_VALUE_LABELS = {
    "create": _("Created"),
    "update": _("Updated"),
    "delete": _("Deleted"),
    "fixed": _("Fixed group"),
    "role": _("Role group"),
    "verified": _("Verified"),
    "trusted": _("Trusted (already in group)"),
    "code": _("Verification code"),
    "manager": _("Confirmed by manager"),
    "conflict": _("Conflict"),
    "takeover": _("Taken over by verification code"),
    "qq_mismatch": _("Applicant QQ mismatch"),
}


def _detail_text(value) -> str:
    if isinstance(value, dict):
        return pgettext("separator between clauses", ", ").join(
            pgettext("audit detail: label and value", "%(label)s: %(value)s")
            % {"label": DETAIL_KEY_LABELS.get(k, k), "value": _detail_text(v)}
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return pgettext("list separator", ", ").join(_detail_text(v) for v in value) or "—"
    if value is None or value == "":
        return "—"
    if value is True:
        return pgettext("qqbot", "Yes")
    if value is False:
        return pgettext("qqbot", "No")
    if isinstance(value, str):
        return str(DETAIL_VALUE_LABELS.get(value, DETAIL_KEY_LABELS.get(value, value)))
    return str(value)


def _audit_row(entry: AuditLog) -> dict:
    detail = entry.detail if isinstance(entry.detail, dict) else {"detail": entry.detail}
    return {
        "entry": entry,
        "actor": entry.actor_name or gettext("Bot / system"),
        "details": [(DETAIL_KEY_LABELS.get(k, k), _detail_text(v)) for k, v in detail.items()],
    }


@login_required
@permission_required(MANAGE, raise_exception=True)
@ui_language_view
def audit_list(request):
    form = AuditFilterForm(request.GET or None)
    qs = AuditLog.objects.order_by("-id")
    if form.is_bound and form.is_valid():
        if form.cleaned_data["qq"]:
            qs = qs.filter(qq=form.cleaned_data["qq"])
        if form.cleaned_data["action"]:
            qs = qs.filter(action=form.cleaned_data["action"])
    elif form.is_bound:
        qs = qs.none()
    page = _page(request, qs)
    return _render(
        request,
        "qqbot/manage/audit.html",
        _ctx(
            "audit",
            form=form,
            page=page,
            rows=[_audit_row(a) for a in page.object_list],
            querystring=_querystring(request),
        ),
    )
