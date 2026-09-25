"""Forms for the QQ manager pages (docs/SPEC.md section 6)."""

from django import forms
from django.contrib.auth.models import Group

from ..core import cards
from ..models import AuditLog, Binding, Config, QQGroup, normalize_qq

QQ_ERROR = "请输入 5–11 位数字（不能以 0 开头）。"


class QQGroupForm(forms.ModelForm):
    """Create / edit a managed QQ group.

    * ``group_id`` is normalized (spaces and full-width digits are fine) and
      must be unique.
    * A role group needs at least one AA group; a fixed group must not have
      any (it is open to every member, so selected groups would be ignored
      and the manager probably meant a role group).
    """

    # Wider than the model field so "123 456 789" or full-width digits reach
    # clean_group_id() and get normalized instead of failing max_length.
    group_id = forms.CharField(
        label="群号",
        max_length=40,
        help_text="QQ 群号，5–11 位数字。",
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
            "name": "显示给成员看的名字。",
            "kind": "固定群：所有成员都可以加入。身份组小群：只有下面所选 AA 组里的人可以加入。",
            "description": "显示在群号旁边的一句话，可以不填。",
            "sort_order": "数字越小越靠前。",
            "is_active": "取消勾选后，成员看不到这个群，机器人也不再管理它。",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["required_groups"].queryset = Group.objects.order_by("name")
        self.fields["required_groups"].help_text = (
            "只有身份组小群需要选择：属于其中任意一个组的人就可以加入。固定群请不要选择。"
        )
        self.fields["kind"].choices = QQGroup.Kind.choices

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("请填写群名称。")
        return name

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

    def clean_group_id(self):
        group_id = normalize_qq(self.cleaned_data.get("group_id"))
        if not group_id:
            raise forms.ValidationError("群号格式不正确：" + QQ_ERROR)
        others = QQGroup.objects.filter(group_id=group_id)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise forms.ValidationError("这个群号已经添加过了，请不要重复添加。")
        return group_id

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        required = cleaned.get("required_groups")
        has_required = required is not None and len(required) > 0
        if kind == QQGroup.Kind.ROLE and required is not None and not has_required:
            self.add_error(
                "required_groups",
                "身份组小群至少要选择一个 AA 组，否则谁都进不了这个群。",
            )
        if kind == QQGroup.Kind.FIXED and has_required:
            self.add_error(
                "required_groups",
                "固定群对所有成员开放，不需要选择 AA 组。"
                "如果这个群只给特定组的人，请把类型改成「身份组小群」；否则请取消勾选所有组。",
            )
        return cleaned


class CardOverrideForm(forms.Form):
    """Manager-set group card. Empty means "use the automatic card"; the
    byte limit is checked by ``core.bindings.set_card_override``."""

    card = forms.CharField(
        label="指定群名片",
        required=False,
        max_length=200,
        help_text=f"留空表示按设置里的格式自动生成。最多 {cards.CARD_MAX_BYTES} 字节（一个汉字占 3 字节）。",
    )


class BindingFilterForm(forms.Form):
    q = forms.CharField(label="搜索", required=False, max_length=100)
    status = forms.ChoiceField(
        label="状态",
        required=False,
        choices=[("", "全部状态")] + list(Binding.Status.choices),
    )
    conflict = forms.BooleanField(label="只看冲突", required=False)


class AuditFilterForm(forms.Form):
    qq = forms.CharField(label="QQ 号", required=False, max_length=40)
    action = forms.ChoiceField(
        label="操作",
        required=False,
        choices=[("", "全部操作")] + list(AuditLog.Action.choices),
    )

    def clean_qq(self):
        raw = (self.cleaned_data.get("qq") or "").strip()
        if not raw:
            return ""
        qq = normalize_qq(raw)
        if not qq:
            raise forms.ValidationError("QQ 号格式不正确：" + QQ_ERROR)
        return qq


class MultilineField(forms.CharField):
    """CharField that normalizes browser line endings (``\r\n`` -> ``\n``)
    before comparing and saving, so an untouched textarea is not "changed"."""

    def to_python(self, value):
        value = super().to_python(value)
        return value.replace("\r\n", "\n").replace("\r", "\n") if value else value


class ConfigForm(forms.ModelForm):
    rules_text = MultilineField(
        label=Config._meta.get_field("rules_text").verbose_name,
        required=False,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="显示在「我的 QQ」页面上，每一行单独显示。",
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
            "code_ttl_minutes": "5–60 分钟。",
            "rebind_cooldown_hours": "成员换绑 QQ 之后，要等这么多小时才能再换。填 0 表示不限制。",
        }

    def clean_card_format(self):
        fmt = (self.cleaned_data.get("card_format") or "").strip()
        if not fmt:
            raise forms.ValidationError("请填写群名片格式。")
        if not cards.format_is_valid(fmt):
            names = "、".join("{" + p + "}" for p in cards.PLACEHOLDERS)
            raise forms.ValidationError(
                f"群名片格式有误。只能使用这些占位符：{names}；"
                "大括号要成对出现，里面不能加别的内容。如果要显示大括号本身，请写成 {{ 或 }}。"
            )
        return fmt
