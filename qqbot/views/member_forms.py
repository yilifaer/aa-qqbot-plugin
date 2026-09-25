"""Forms for the member actions in the services card.

The forms only parse the POST data. All real validation (QQ format,
nickname rules) happens in ``qqbot.core`` so that the rules live in one
place; the core result message is shown to the member.

服务卡片里成员操作用的表单。

这些表单只负责解析 POST 数据。真正的校验（QQ 格式、昵称规则）都在
``qqbot.core`` 里做，这样规则只放在一个地方；成员看到的是 core 返回的
结果信息。
"""

from django import forms

from ..core.util import NICKNAME_MAX_LENGTH

_REQUIRED_QQ = {"required": "请填写 QQ 号。"}
_REQUIRED_NICKNAME = {"required": "请填写昵称。"}


class SubmitForm(forms.Form):
    """Bind, re-bind or regenerate a code (``regenerate`` re-uses the live
    code's QQ and nickname, so the fields are optional then).

    绑定、换绑或重新生成验证码（``regenerate`` 会沿用当前有效验证码的 QQ 和
    昵称，所以这时这两个字段可以不填）。
    """

    qq = forms.CharField(
        label="QQ 号",
        max_length=32,
        required=False,
        error_messages=_REQUIRED_QQ,
    )
    nickname = forms.CharField(
        label="昵称",
        max_length=64,
        required=False,
        error_messages=_REQUIRED_NICKNAME,
    )
    regenerate = forms.BooleanField(required=False)

    def clean(self):
        data = super().clean()
        if not data.get("regenerate"):
            if not (data.get("qq") or "").strip():
                self.add_error("qq", _REQUIRED_QQ["required"])
            if not (data.get("nickname") or "").strip():
                self.add_error("nickname", _REQUIRED_NICKNAME["required"])
        return data


class NicknameForm(forms.Form):
    nickname = forms.CharField(
        label="昵称",
        max_length=64,
        error_messages=_REQUIRED_NICKNAME,
    )


class UnbindForm(forms.Form):
    """Unbinding needs the ticked "我确认要解除绑定" box (no separate page).

    解绑前必须勾选「我确认要解除绑定」（没有单独的确认页面）。
    """

    confirm = forms.BooleanField(
        required=True,
        error_messages={"required": "请先勾选「我确认要解除绑定」，再点「解除绑定」。"},
    )


def first_error(form) -> str:
    """The first error message of a bound, invalid form.

    返回一个已提交数据、但没通过校验的表单里的第一条错误信息。
    """
    for errors in form.errors.values():
        for error in errors:
            return str(error)
    return "提交的内容有误，请检查后重试。"


NICKNAME_HELP = f"最长 {NICKNAME_MAX_LENGTH} 个字；只能用中文、英文字母、数字、空格和 _ - . · ( )。"
