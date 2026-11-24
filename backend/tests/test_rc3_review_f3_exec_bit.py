"""RC3 定向整改 F-3：模拟钩子 simhook.sh 必须以其可执行位（100755）提交到 Git。

反例（75073de 基线）：Git 树中 deploy/windows/tests/sim/simhook.sh 模式为 100644。`common.ps1` 通过
`& $env:ISH_SIM_HOOK 'stop' ...` 直接执行该脚本；在全新克隆目录（未手工 chmod）里因此报“权限被拒绝”，
升级/恢复模拟无法按 README 直接运行（修复前仅 50/83 通过）。

本门禁：断言 Git 索引中该文件的模式为 100755，且首行为 bash shebang。任何把执行位改回 100644 的提交都会让本测试失败。
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SIMHOOK = "deploy/windows/tests/sim/simhook.sh"


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, OSError):
        pytest.skip("git 不可用，跳过执行位门禁")


def test_simhook_is_executable_in_git():
    r = _git("ls-files", "--stage", "--", SIMHOOK)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert line, f"{SIMHOOK} 未被 Git 跟踪"
    mode = line.split()[0]
    assert mode == "100755", f"{SIMHOOK} 必须以可执行模式 100755 提交（防止全新克隆无 chmod 时无法直接执行），实际：{mode}"


def test_simhook_has_bash_shebang():
    first = (ROOT / SIMHOOK).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    assert first.strip() == "#!/bin/bash", f"{SIMHOOK} 首行应为 #!/bin/bash，实际：{first!r}"
