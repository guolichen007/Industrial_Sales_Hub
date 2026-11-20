"""故障注入（仅测试机）：用于在真实 Windows/Linux 环境中验证恢复与升级的回滚路径。

只有同时满足以下条件才生效，正式主机默认永不触发：
- 环境变量 ISH_FAULT_INJECT=<阶段名>[,<阶段名>...]；
- DATA_DIR 下存在管理员手工创建的文件 ALLOW_FAULT_INJECTION（测试机标记）。
"""
import os

from django.conf import settings


class InjectedFault(RuntimeError):
    pass


def fault(stage: str):
    names = {x.strip() for x in os.environ.get("ISH_FAULT_INJECT", "").split(",") if x.strip()}
    if stage in names and (settings.DATA_DIR / "ALLOW_FAULT_INJECTION").exists():
        raise InjectedFault(f"故障注入：{stage}")
