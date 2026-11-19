from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.common.roles import ROLE_CHOICES, SALES


class User(AbstractUser):
    """内部员工账号。一个账号一个主角色（首期 1～5 人，足够且易于审计）。"""

    role = models.CharField("角色", max_length=16, choices=ROLE_CHOICES, default=SALES)
    display_name = models.CharField("姓名", max_length=50, blank=True)
    phone = models.CharField("电话", max_length=30, blank=True)

    class Meta:
        verbose_name = "用户"
        ordering = ["id"]

    @property
    def name(self):
        return self.display_name or self.username
