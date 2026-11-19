"""首次初始化正式管理员（安装脚本调用，交互式输入密码；不接受命令行明文密码）。

用法：python manage.py init_admin --username <用户名> [--name 姓名]
- 仅当系统中不存在任何启用的管理员时允许执行；
- 密码通过交互输入（两次确认），或通过环境变量 ISH_INIT_ADMIN_PASSWORD 传入（仅供无人值守自动化测试）。
"""
import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.common.roles import ADMIN


class Command(BaseCommand):
    help = "创建首个正式管理员（仅在没有管理员时可用）"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--name", default="系统管理员")
        parser.add_argument("--if-missing", action="store_true", help="已有管理员时直接成功退出（安装脚本重复执行用）")

    def handle(self, *args, username, name, if_missing, **kw):
        if User.objects.filter(role=ADMIN, is_active=True).exists():
            if if_missing:
                self.stdout.write("已存在管理员，跳过初始化。")
                return
            raise CommandError("系统已存在管理员；请登录后在“系统设置 → 账号与角色”中管理账号。")
        if User.objects.filter(username=username).exists():
            raise CommandError(f"用户名 {username} 已存在")
        pwd = os.environ.get("ISH_INIT_ADMIN_PASSWORD")
        if not pwd:
            for _ in range(3):
                p1 = getpass.getpass("请输入管理员密码（至少 8 位，含字母和数字）：")
                p2 = getpass.getpass("请再次输入：")
                if p1 != p2:
                    self.stderr.write("两次输入不一致，请重试。")
                    continue
                pwd = p1
                break
            if not pwd:
                raise CommandError("未设置密码，已取消。")
        u = User(username=username, display_name=name, role=ADMIN, is_staff=False)
        try:
            validate_password(pwd, u)
        except ValidationError as e:
            raise CommandError("密码不符合要求：" + "；".join(e.messages))
        u.set_password(pwd)
        u.save()
        self.stdout.write(self.style.SUCCESS(f"管理员 {username} 已创建。请登录后创建负责人、商务、销售、财务等业务账号。"))
