from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.conf import settings
from .models import QQBinding

def ping(request):
    return HttpResponse("pong")

@login_required
def bind(request):
    # 支持 GET/POST 两种提交
    qq = request.POST.get("qq") or request.GET.get("qq")
    nickname = request.POST.get("nickname") or request.GET.get("nickname")
    corp_ticket = request.POST.get("ticket") or request.GET.get("ticket") or ""

    if not qq or not nickname:
        return JsonResponse({"ok": False, "error": "缺少 qq 或 nickname"}, status=400)

    # 尝试从 AA 里拿主角色信息（拿不到也不报错）
    main_character_id = None
    main_character_name = ""
    try:
        profile = request.user.profile
        main_char = getattr(profile, "main_character", None)
        if main_char:
            main_character_id = getattr(main_char, "character_id", None)
            main_character_name = getattr(main_char, "character_name", "") or ""
    except Exception:
        pass

    QQBinding.objects.update_or_create(
        user=request.user,
        defaults=dict(
            qq=qq,
            nickname=nickname,
            corp_ticket=corp_ticket,
            main_character_id=main_character_id,
            main_character_name=main_character_name,
        )
    )

    return JsonResponse({
        "ok": True,
        "msg": "绑定成功",
        "groups": {
            "IGCCN-QQ": getattr(settings, "QQBOT_GROUP_CHAT", ""),
            "IGCCN-Ping": getattr(settings, "QQBOT_PING_GROUP", ""),
        },
    })
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render
from .models import QQBinding

@login_required
@permission_required("qqbot.view_qqbinding", raise_exception=True)
def binding_list(request):
    items = QQBinding.objects.select_related("user").order_by("-created_at")
    return render(request, "qqbot/bindings_list.html", {"items": items, "title": "QQ绑定管理列表"})
