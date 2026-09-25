"""QQ manager pages (docs/SPEC.md section 6, DESIGN.md 4.3).

Every view needs a login and ``qqbot.manage``; changes are POST-only.
Binding changes go through ``qqbot.core.bindings``; group and settings
saves emit events and audit records through ``qqbot.core``.
Managers see full QQ numbers.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from allianceauth.services.hooks import get_extension_logger

from .. import tasks
from ..core import audit, bindings, cards, eligibility, events
from ..core.roster import fresh_roster_cutoff, unbound_roster
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

REASON_LABELS = {
    eligibility.OK: "合格",
    eligibility.NOT_BOUND: "未绑定",
    eligibility.PENDING_VERIFY: "待验证",
    eligibility.CONFLICT: "冲突",
    eligibility.USER_INACTIVE: "账号已停用",
    eligibility.NO_MAIN: "没有主角色",
    eligibility.NO_ACCESS: "没有成员权限",
    eligibility.GROUP_ROLE_MISSING: "不在要求的组",
    eligibility.GROUP_MISCONFIGURED: "群配置有误",
}
DECISION_LABELS = {
    eligibility.ALLOW: "允许",
    eligibility.DENY: "不允许",
    eligibility.REVIEW: "需人工处理",
}
DECISION_BADGES = {
    eligibility.ALLOW: "text-bg-success",
    eligibility.DENY: "text-bg-danger",
    eligibility.REVIEW: "text-bg-warning",
}

# Where a POST action may send the manager back to (no free-form URLs).
BACK_TARGETS = {"pending": "qqbot:manage_pending", "bindings": "qqbot:manage_bindings"}

BINDING_RELATED = ("user", "user__profile", "user__profile__main_character")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _conflict_qqs() -> list[str]:
    return [qq for qq, _rows in bindings.conflicts()]


def _fresh_unbound():
    """Unbound roster entries of active groups whose roster is fresh."""
    return unbound_roster().filter(
        group__last_roster_at__isnull=False,
        group__last_roster_at__gte=fresh_roster_cutoff(),
    )


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
    ctx = {"qqbot_nav": "manage", "manage_tab": tab, "summary": _summary()}
    ctx.update(extra)
    return ctx


def _message(request, result) -> None:
    level = messages.SUCCESS if result.ok else messages.ERROR
    if result.ok and result.outcome == "unchanged":
        level = messages.INFO
    messages.add_message(
        request, level, result.message or ("操作完成。" if result.ok else "操作失败。")
    )


def _back(request, default: str, **kwargs):
    target = BACK_TARGETS.get(request.POST.get("back", ""))
    if target:
        return redirect(target)
    return redirect(default, **kwargs)


def _page(request, qs):
    return Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))


def _querystring(request) -> str:
    """Current GET parameters without ``page`` (for pagination links)."""
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
# --------------------------------------------------------------------------


@login_required
@permission_required(MANAGE, raise_exception=True)
def index(request):
    return redirect("qqbot:manage_bindings")


@login_required
@permission_required(MANAGE, raise_exception=True)
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
    return render(request, "qqbot/manage/groups.html", _ctx("groups", rows=rows))


def _save_group(request, form, op: str):
    """Save the group form; returns the group or None (errors on the form)."""
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
        form.add_error("group_id", "这个群号已经添加过了，请不要重复添加。")
        return None
    return group


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
def group_create(request):
    form = QQGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = _save_group(request, form, "create")
        if group is not None:
            messages.success(request, f"已添加群「{group.name}」。")
            return redirect("qqbot:manage_groups")
    return render(
        request, "qqbot/manage/group_form.html", _ctx("groups", form=form, group=None)
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
def group_edit(request, pk):
    group = get_object_or_404(QQGroup, pk=pk)
    form = QQGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        if not form.has_changed():
            messages.info(request, "没有需要修改的内容。")
            return redirect("qqbot:manage_groups")
        group = _save_group(request, form, "update")
        if group is not None:
            messages.success(request, f"已保存群「{group.name}」。")
            return redirect("qqbot:manage_groups")
    return render(
        request, "qqbot/manage/group_form.html", _ctx("groups", form=form, group=group)
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
def group_delete(request, pk):
    group = get_object_or_404(QQGroup, pk=pk)
    if request.method == "POST":
        with transaction.atomic():
            detail = _group_audit_detail(group)
            name = group.name
            group.delete()
            events.emit_groups_changed()
            audit.log(AuditLog.Action.GROUP, actor=request.user, op="delete", **detail)
        messages.success(request, f"已删除群「{name}」。")
        return redirect("qqbot:manage_groups")
    return render(
        request,
        "qqbot/manage/group_delete.html",
        _ctx("groups", group=group, roster_count=RosterEntry.objects.filter(group=group).count()),
    )


# --------------------------------------------------------------------------
# bindings
# --------------------------------------------------------------------------


@login_required
@permission_required(MANAGE, raise_exception=True)
def binding_list(request):
    form = BindingFilterForm(request.GET or None)
    qs = Binding.objects.select_related(*BINDING_RELATED).order_by("user__username", "pk")
    conflict_qqs = set(_conflict_qqs())
    form.is_valid()  # a bad value in one field must not drop the others
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
    return render(
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
        conflict=(
            binding.status == Binding.Status.TRUSTED and bool(others) and not other_verified
        ),
        history=[_audit_row(a) for a in history],
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
def binding_detail(request, pk):
    binding = _get_binding(pk)
    return render(request, "qqbot/manage/binding_detail.html", _binding_context(binding))


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_POST
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
    return render(
        request,
        "qqbot/manage/binding_detail.html",
        _binding_context(binding, card_form=form),
        status=400,
    )


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_POST
def binding_confirm(request, pk):
    binding = _get_binding(pk)
    _message(request, bindings.confirm(binding, request.user))
    return _back(request, "qqbot:manage_binding", pk=binding.pk)


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
def binding_unbind(request, pk):
    binding = _get_binding(pk)
    if request.method == "POST":
        result = bindings.unbind(binding.user, actor=request.user, forced=True)
        _message(request, result)
        return _back(request, "qqbot:manage_bindings")
    back = request.GET.get("back", "")
    return render(
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
# --------------------------------------------------------------------------


@login_required
@permission_required(MANAGE, raise_exception=True)
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
    cutoff = fresh_roster_cutoff(config=config)
    stale_groups = list(
        QQGroup.objects.filter(is_active=True)
        .filter(Q(last_roster_at__isnull=True) | Q(last_roster_at__lt=cutoff))
        .order_by("kind", "sort_order", "name")
    )
    return render(
        request,
        "qqbot/manage/pending.html",
        _ctx(
            "pending",
            conflicts=conflict_rows,
            unbound_groups=list(by_group.values()),
            stale_groups=stale_groups,
            roster_max_age_days=config.roster_max_age_days,
        ),
    )


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


_CONFIG_SHORT_FIELDS = ("card_format", "code_ttl_minutes", "roster_max_age_days",
                        "rebind_cooldown_hours")


@login_required
@permission_required(MANAGE, raise_exception=True)
@require_http_methods(["GET", "POST"])
def settings_view(request):
    config = Config.get_solo()
    before = {f: getattr(config, f) for f in _CONFIG_SHORT_FIELDS}
    form = ConfigForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        changed = list(form.changed_data)
        if not changed:
            messages.info(request, "没有需要修改的内容。")
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
                transaction.on_commit(tasks.queue_reconcile)
        if "card_format" in changed:
            messages.success(request, "设置已保存。群名片格式已修改，所有人的群名片会在后台重新计算。")
        else:
            messages.success(request, "设置已保存。")
        return redirect("qqbot:manage_settings")
    return render(
        request,
        "qqbot/manage/settings.html",
        _ctx(
            "settings",
            form=form,
            placeholders=cards.PLACEHOLDERS,
            example_card=cards.preview_card(request.user, "昵称", config),
        ),
    )


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


# Chinese labels for the keys and well-known values in AuditLog.detail.
DETAIL_KEY_LABELS = {
    "op": "操作",
    "changed": "修改了",
    "group_id": "群号",
    "name": "群名称",
    "kind": "类型",
    "required_groups": "需要的 AA 组",
    "description": "说明",
    "sort_order": "排序",
    "is_active": "启用",
    "status": "状态",
    "previous_status": "原来的状态",
    "old": "原来",
    "new": "改为",
    "old_qq": "原来的 QQ",
    "others": "其他认领的账号",
    "reason": "原因",
    "winner": "胜出的账号",
    "via": "方式",
    "expires_at": "有效期到",
    "applicant_qq": "申请人 QQ",
    "rules_text": "入群须知",
    "card_format": "群名片格式",
    "code_ttl_minutes": "验证码有效期（分钟）",
    "roster_max_age_days": "群成员名单有效期（天）",
    "rebind_cooldown_hours": "换绑冷却（小时）",
}
DETAIL_VALUE_LABELS = {
    "create": "新增",
    "update": "修改",
    "delete": "删除",
    "fixed": "固定群",
    "role": "身份组小群",
    "verified": "已验证",
    "trusted": "老成员免验证",
    "code": "验证码",
    "manager": "管理员确认",
    "conflict": "冲突",
    "takeover": "被验证码接管",
    "qq_mismatch": "申请人 QQ 不一致",
}


def _detail_text(value) -> str:
    if isinstance(value, dict):
        return "，".join(
            f"{DETAIL_KEY_LABELS.get(k, k)} {_detail_text(v)}" for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return "、".join(_detail_text(v) for v in value) or "—"
    if value is None or value == "":
        return "—"
    if value is True:
        return "是"
    if value is False:
        return "否"
    if isinstance(value, str):
        return DETAIL_VALUE_LABELS.get(value, DETAIL_KEY_LABELS.get(value, value))
    return str(value)


def _audit_row(entry: AuditLog) -> dict:
    detail = entry.detail if isinstance(entry.detail, dict) else {"detail": entry.detail}
    return {
        "entry": entry,
        "actor": entry.actor_name or "机器人 / 系统",
        "details": [(DETAIL_KEY_LABELS.get(k, k), _detail_text(v)) for k, v in detail.items()],
    }


@login_required
@permission_required(MANAGE, raise_exception=True)
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
    return render(
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
