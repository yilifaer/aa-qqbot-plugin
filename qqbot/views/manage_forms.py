"""Forms for the QQ manager pages (docs/SPEC.md section 6).

QQ 管理员页面用到的表单（docs/SPEC.md 第 6 节）。
"""

from django import forms
from django.contrib.auth.models import Group
from django.utils.functional import lazy
from django.utils.translation import gettext, pgettext
from django.utils.translation import gettext_lazy as _

from ..core import cards
from ..models import AuditLog, Binding, Config, QQGroup, normalize_qq

QQ_ERROR = _("Enter 5–11 digits (not starting with 0).")


class QQGroupForm(forms.ModelForm):
    """Create / edit a managed QQ group.

    * ``group_id`` is normalized (spaces and full-width digits are fine) and
      must be unique.
    * A role group needs at least one AA group; a fixed group must not have
      any (it is open to every member, so selected groups would be ignored
      and the manager probably meant a role group).

    新增 / 编辑一个受管理的 QQ 群。

    * ``group_id`` 会先规范化（带空格或全角数字都可以），并且不能重复。
    * 身份组小群至少要选一个 AA 组；固定群一个组都不能选（固定群对所有成员
      开放，选了的组会被忽略，管理员多半其实是想建身份组小群）。
    """

    # Wider than the model field so "123 456 789" or full-width digits reach
    # clean_group_id() and get normalized instead of failing max_length.
    # 比模型字段更长，这样 "123 456 789" 或全角数字能进入 clean_group_id()
    # 被规范化，而不是直接因为超过 max_length 报错。
    group_id = forms.CharField(
        label=_("Group number"),
        max_length=40,
        help_text=_("QQ group number, 5–11 digits."),
    )

    class Meta:
        model = QQGroup
        fields = (
            "name",
            "group_id",
            "kind",
            "required_groups",
            "description",
            "sort_order",
            "is_active",
        )
        widgets = {
            "kind": forms.RadioSelect,
            "required_groups": forms.CheckboxSelectMultiple,
        }
        help_texts = {
            "name": _("The name members see."),
            "kind": _(
                "Fixed group: any member can join. Role group: only people in the "
                "AA groups selected below can join."
            ),
            "description": _("A short line shown next to the group number. Optional."),
            "sort_order": _("Lower numbers are listed first."),
            "is_active": _(
                "When unticked, members don't see this group and the bot stops managing it."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["required_groups"].queryset = Group.objects.order_by("name")
        self.fields["required_groups"].help_text = _(
            "Only for role groups: anyone in any of these groups can join. "
            "Leave empty for a fixed group."
        )
        self.fields["kind"].choices = QQGroup.Kind.choices

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError(gettext("Enter a group name."))
        return name

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

    def clean_group_id(self):
        group_id = normalize_qq(self.cleaned_data.get("group_id"))
        if not group_id:
            raise forms.ValidationError(
                gettext("Invalid group number: %(hint)s") % {"hint": QQ_ERROR}
            )
        others = QQGroup.objects.filter(group_id=group_id)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise forms.ValidationError(
                gettext("This group number has already been added; don't add it twice.")
            )
        return group_id

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        required = cleaned.get("required_groups")
        has_required = required is not None and len(required) > 0
        if kind == QQGroup.Kind.ROLE and required is not None and not has_required:
            self.add_error(
                "required_groups",
                gettext(
                    "A role group needs at least one AA group, otherwise nobody can join it."
                ),
            )
        if kind == QQGroup.Kind.FIXED and has_required:
            self.add_error(
                "required_groups",
                gettext(
                    "A fixed group is open to every member and needs no AA groups. "
                    'If this group is only for certain groups, change the type to "Role group"; '
                    "otherwise untick all groups."
                ),
            )
        return cleaned


def _card_help_text() -> str:
    return gettext(
        "Leave empty to generate it from the format in Settings. At most %(max_bytes)s "
        "bytes (a Chinese character takes 3 bytes)."
    ) % {"max_bytes": cards.CARD_MAX_BYTES}


class CardOverrideForm(forms.Form):
    """Manager-set group card. Empty means "use the automatic card"; the
    byte limit is checked by ``core.bindings.set_card_override``.

    管理员手动指定的群名片。留空表示“使用自动生成的群名片”；
    字节数上限由 ``core.bindings.set_card_override`` 检查。
    """

    card = forms.CharField(
        label=_("Custom group nickname"),
        required=False,
        max_length=200,
        help_text=lazy(_card_help_text, str)(),
    )


class BindingFilterForm(forms.Form):
    q = forms.CharField(label=_("Search"), required=False, max_length=100)
    status = forms.ChoiceField(
        label=_("Status"),
        required=False,
        choices=[("", _("All statuses"))] + list(Binding.Status.choices),
    )
    conflict = forms.BooleanField(label=_("Conflicts only"), required=False)


class AuditFilterForm(forms.Form):
    qq = forms.CharField(label=_("QQ number"), required=False, max_length=40)
    action = forms.ChoiceField(
        label=_("Action"),
        required=False,
        choices=[("", _("All actions"))] + list(AuditLog.Action.choices),
    )

    def clean_qq(self):
        raw = (self.cleaned_data.get("qq") or "").strip()
        if not raw:
            return ""
        qq = normalize_qq(raw)
        if not qq:
            raise forms.ValidationError(
                gettext("Invalid QQ number: %(hint)s") % {"hint": QQ_ERROR}
            )
        return qq


class MultilineField(forms.CharField):
    """CharField that normalizes browser line endings (``\r\n`` -> ``\n``)
    before comparing and saving, so an untouched textarea is not "changed".

    一个 CharField：比较和保存之前，先统一浏览器提交的换行符（``\r\n`` -> ``\n``），
    这样没动过的多行文本框不会被当成“已修改”。
    """

    def to_python(self, value):
        value = super().to_python(value)
        return value.replace("\r\n", "\n").replace("\r", "\n") if value else value


class ConfigForm(forms.ModelForm):
    rules_text = MultilineField(
        label=Config._meta.get_field("rules_text").verbose_name,
        required=False,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text=_(
            "Shown in small print under the QQ binding card on the Services page; "
            "each line is shown separately."
        ),
    )

    class Meta:
        model = Config
        fields = (
            "rules_text",
            "card_format",
            "code_ttl_minutes",
            "roster_max_age_days",
            "rebind_cooldown_hours",
        )
        help_texts = {
            "code_ttl_minutes": _("5–60 minutes."),
            "rebind_cooldown_hours": _(
                "After a member changes QQ, they must wait this many hours before changing "
                "again. 0 means no limit."
            ),
        }

    def clean_card_format(self):
        fmt = (self.cleaned_data.get("card_format") or "").strip()
        if not fmt:
            raise forms.ValidationError(gettext("Enter a group nickname format."))
        if not cards.format_is_valid(fmt):
            names = pgettext("list separator", ", ").join(
                "{" + p + "}" for p in cards.PLACEHOLDERS
            )
            raise forms.ValidationError(
                gettext(
                    "Invalid group nickname format. Only these placeholders are allowed: "
                    "%(names)s; braces must come in pairs with nothing else inside. To show "
                    "a brace itself, write {{ or }}."
                )
                % {"names": names}
            )
        return fmt
