from django import forms
from .models import QQBinding

class QQBindForm(forms.ModelForm):
    class Meta:
        model = QQBinding
        fields = ["qq", "nickname", "corp_ticket"]
        labels = {
            "qq": "QQ号",
            "nickname": "昵称",
            "corp_ticket": "军团Ticket（可选）",
        }
