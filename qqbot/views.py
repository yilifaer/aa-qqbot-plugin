from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.contrib import messages

from .models import QQBinding
from .forms import QQBindForm


def ping(request):
    """Health check endpoint."""
    return HttpResponse("pong")


def _get_main_character(user):
    """Try to get user's main EVE character from AA profile."""
    main_character_id = None
    main_character_name = ""
    corp_ticker = ""
    try:
        profile = user.profile
        main_char = getattr(profile, "main_character", None)
        if main_char:
            main_character_id = getattr(main_char, "character_id", None)
            main_character_name = getattr(main_char, "character_name", "") or ""
            corp_ticker = getattr(main_char, "corporation_ticker", "") or ""
    except Exception:
        pass
    return main_character_id, main_character_name, corp_ticker


@login_required
def bind(request):
    """Show binding form (GET) or save binding (POST)."""
    main_id, main_name, corp_ticker = _get_main_character(request.user)

    # Check if user already has a binding
    binding = QQBinding.objects.filter(user=request.user).first()

    if request.method == "POST":
        form = QQBindForm(request.POST, instance=binding)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.main_character_id = main_id
            obj.main_character_name = main_name
            # Auto-fill corp ticket from main character if user didn't provide one
            if not obj.corp_ticket and corp_ticker:
                obj.corp_ticket = corp_ticker
            obj.save()

            context = {
                "ok": True,
                "binding": obj,
                "group_chat": getattr(settings, "QQBOT_GROUP_CHAT", ""),
                "ping_group": getattr(settings, "QQBOT_PING_GROUP", ""),
                "main_char_id": main_id,
                "main_char_name": main_name,
                "form": form,
            }
            return render(request, "qqbot/bind.html", context)
    else:
        # GET: show form, pre-fill with existing binding if any
        form = QQBindForm(instance=binding)

    context = {
        "ok": False,
        "binding": binding,
        "form": form,
        "main_char_id": main_id,
        "main_char_name": main_name,
        "group_chat": getattr(settings, "QQBOT_GROUP_CHAT", ""),
        "ping_group": getattr(settings, "QQBOT_PING_GROUP", ""),
    }
    return render(request, "qqbot/bind.html", context)


@login_required
def bind_api(request):
    """Legacy JSON API endpoint (kept for backward compat with external callers)."""
    qq = request.POST.get("qq") or request.GET.get("qq")
    nickname = request.POST.get("nickname") or request.GET.get("nickname")
    corp_ticket = request.POST.get("ticket") or request.GET.get("ticket") or ""

    if not qq or not nickname:
        return JsonResponse(
            {"ok": False, "error": "缺少 qq 或 nickname"}, status=400
        )

    main_id, main_name, _ = _get_main_character(request.user)

    QQBinding.objects.update_or_create(
        user=request.user,
        defaults=dict(
            qq=qq,
            nickname=nickname,
            corp_ticket=corp_ticket,
            main_character_id=main_id,
            main_character_name=main_name,
        ),
    )

    return JsonResponse(
        {
            "ok": True,
            "msg": "绑定成功",
            "groups": {
                "IGCCN-QQ": getattr(settings, "QQBOT_GROUP_CHAT", ""),
                "IGCCN-Ping": getattr(settings, "QQBOT_PING_GROUP", ""),
            },
        }
    )


@login_required
@permission_required("qqbot.view_qqbinding", raise_exception=True)
def binding_list(request):
    """Staff-only listing of all QQ bindings."""
    items = QQBinding.objects.select_related("user").order_by("-created_at")
    return render(
        request,
        "qqbot/bindings_list.html",
        {"items": items, "title": "QQ 绑定管理列表"},
    )
