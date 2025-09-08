from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class QQBinding(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="qq_binding")
    main_character_id = models.BigIntegerField(null=True, blank=True, verbose_name="主角ID")
    main_character_name = models.CharField(max_length=128, blank=True, verbose_name="主角名称")
    corp_ticket = models.CharField(max_length=64, blank=True, verbose_name="军团Ticket")
    qq = models.CharField(max_length=20, verbose_name="QQ号")
    nickname = models.CharField(max_length=64, verbose_name="昵称")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "QQ绑定"
        verbose_name_plural = "QQ绑定"

    def __str__(self):
        return f"{self.user.username} - QQ:{self.qq}"
