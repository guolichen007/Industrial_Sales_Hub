import subprocess
from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.common import ops_python


@pytest.fixture(autouse=True)
def ops_base(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_python, "settings", SimpleNamespace(BASE_DIR=tmp_path))


def test_management_child_forces_utf8_and_uses_binary_pipes(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp936")
    (tmp_path / "manage.py").write_text(
        "import sys\n"
        "print(sys.stdout.encoding, sys.dont_write_bytecode)\n"
        "print('完整性校验通过')\n",
        encoding="utf-8",
    )
    result = ops_python.run_ops_manage(["verify_integrity"], {})
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["utf-8 True", "完整性校验通过"]


@pytest.mark.parametrize(
    ("stdout", "stderr", "message"),
    [
        (None, b"", "缺少 stdout"),
        (b"", b"\xff", "stderr 不是有效 UTF-8"),
        (b"\xff", b"", "stdout 不是有效 UTF-8"),
        ("其他输出".encode("utf-8"), b"", "未返回完整性校验成功标记"),
    ],
)
def test_management_child_rejects_unusable_integrity_output(monkeypatch, stdout, stderr, message):
    monkeypatch.setattr(
        ops_python.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout, stderr)
    )
    with pytest.raises(CommandError, match=message):
        ops_python.run_ops_manage(["verify_integrity"], {})


def test_management_child_preserves_rejected_integrity_result_and_redacts_url(monkeypatch):
    url = "postgres://fixture:qa-password@127.0.0.1:65432/fixture"
    message = f"完整性校验失败 {url}"
    monkeypatch.setattr(
        ops_python.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 24, b"", message.encode("utf-8")),
    )
    result = ops_python.run_ops_manage(["verify_integrity"], {"DATABASE_URL": url})
    assert result.returncode == 24
    assert result.stderr == "完整性校验失败 [DATABASE_URL]"
