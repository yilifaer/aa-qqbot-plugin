from django import forms
from .models import QQBinding


class QQBindForm(forms.ModelForm):
    class Meta:
        model = QQBinding
        fields = ["qq", "nickname", "corp_ticket"]
        labels = {
            "qq": "QQ号",
            "nickname": "昵称",
            "corp_ticket": "军团Ticket（可选，留空将自动使用主角色军团）",
        }
        widgets = {
            "qq": forms.TextInput(attrs={"class": "form-control", "placeholder": "请输入您的QQ号"}),
            "nickname": forms.TextInput(attrs={"class": "form-control", "placeholder": "请输入您的昵称"}),
            "corp_ticket": forms.TextInput(attrs={"class": "form-control", "placeholder": "可选"}),
        }
