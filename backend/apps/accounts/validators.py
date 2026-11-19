import re

from django.core.exceptions import ValidationError


class CharacterClassValidator:
    """密码需同时包含字母与数字（正式部署的最低口令复杂度）。"""

    def validate(self, password, user=None):
        if not (re.search(r"[A-Za-z]", password or "") and re.search(r"\d", password or "")):
            raise ValidationError("密码必须同时包含字母和数字", code="password_classes")

    def get_help_text(self):
        return "密码必须同时包含字母和数字。"
