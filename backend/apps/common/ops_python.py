"""Run a Python management command with a checked UTF-8 pipe contract."""

import subprocess
import sys

from django.conf import settings
from django.core.management.base import CommandError

from .opslock import child_env


def run_ops_manage(args, extra_env):
    env = {key: value for key, value in child_env(extra_env).items() if key.upper() != "PYTHONIOENCODING"}
    # The embedded interpreter's startup settings can differ from the parent.
    # Specify the child mode and decode bytes outside Windows' pipe reader threads.
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(settings.BASE_DIR / "manage.py"), *args],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
        )
    except OSError as error:
        raise CommandError(f"无法启动 {args[0]}（系统错误 {error.errno}）") from error
    for stream in ("stdout", "stderr"):
        raw = getattr(result, stream)
        if not isinstance(raw, bytes):
            raise CommandError(f"{args[0]} 缺少 {stream} 捕获结果（退出码 {result.returncode}）")
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CommandError(
                f"{args[0]} 的 {stream} 不是有效 UTF-8（字节偏移 {error.start}，退出码 {result.returncode}）"
            ) from error
        if url := env.get("DATABASE_URL"):
            value = value.replace(url, "[DATABASE_URL]")
        setattr(result, stream, value)
    if args[0] == "verify_integrity" and result.returncode == 0:
        if "完整性校验通过" not in result.stdout.splitlines():
            raise CommandError("verify_integrity 未返回完整性校验成功标记（退出码 0）")
    return result
