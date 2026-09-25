"""Forms for the member pages.

The forms only parse the POST data. All real validation (QQ format,
nickname rules) happens in ``qqbot.core`` so that the rules live in one
place; the core result message is shown to the member.
"""

from django import forms

from ..core.util import NICKNAME_MAX_LENGTH

_REQUIRED_QQ = {"required": "请填写 QQ 号。"}
_REQUIRED_NICKNAME = {"required": "请填写昵称。"}


class SubmitForm(forms.Form):
    """Bind, re-bind or regenerate a code (``regenerate`` re-uses the live
    code's QQ and nickname, so the fields are optional then)."""

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


def first_error(form) -> str:
    """The first error message of a bound, invalid form."""
    for errors in form.errors.values():
        for error in errors:
            return str(error)
    return "提交的内容有误，请检查后重试。"


NICKNAME_HELP = f"最长 {NICKNAME_MAX_LENGTH} 个字；只能用中文、英文字母、数字、空格和 _ - . · ( )。"
