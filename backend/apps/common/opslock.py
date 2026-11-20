"""运维全局互斥（RC2-R05）：备份、恢复、升级、安装初始化之间跨进程互斥 + 带 owner token 的维护标记。

机制
----
1. DATA_DIR/ops.lock：操作系统级排他锁。
   - Python：保持文件句柄打开并加锁（Windows msvcrt.locking / POSIX fcntl.flock）；
   - PowerShell（scripts/common.ps1 Enter-OpsLock）：以 FileShare.None 打开同一文件并保持到脚本结束。
   两种方式互相可见：任何一方持有时，另一方打开/加锁失败即视为“忙”。进程崩溃时操作系统自动释放锁。
2. DATA_DIR/ops.owner.json：持锁者写入 {task, token, pid, host, started_at}，用于“谁在忙”的提示与崩溃诊断；
   每次获得锁都追加一行到 DATA_DIR/ops.history.log。
3. 继承：持锁的父进程（例如 PowerShell 恢复/升级脚本）通过环境变量 ISH_OPS_TOKEN 把 token 传给它调用的
   manage.py 子进程；子进程发现 token 与 ops.owner.json 一致时视为“已在同一运维任务内”，不再重复加锁。
4. 维护标记 DATA_DIR/MAINTENANCE：内容为 JSON {token, task, pid, at}。只能由同一 token 删除；
   发现他人标记时拒绝开始新的运维任务（例如升级失败后保持的只读维护状态），由管理员按手册处理。

一致性边界（如实说明）：维护窗口内中间件拒绝 /api/ 业务写请求；登录、登出、CSRF 不受限，登录会更新
auth_user.last_login 并写审计记录。这些非业务写入可能落在 pg_dump 快照之后，属于已知豁免，不影响业务数据一致性。
"""
import contextlib
import contextvars
import json
import os
import secrets
import socket
import time
from datetime import datetime

from django.conf import settings


_current = contextvars.ContextVar("ish_ops_token", default=None)  # 同一线程/调用链内嵌套（call_command）
_own_tokens = set()  # 本进程自己创建的 token：不能被本进程其他线程当作“父进程传入”的 token


class OpsBusy(RuntimeError):
    """另一个运维任务正在进行。"""


class MaintenanceHeld(RuntimeError):
    """维护标记被其他任务持有（可能是进行中的任务，或失败后保持的只读维护状态）。"""


def _paths():
    d = settings.DATA_DIR
    return d / "ops.lock", d / "ops.owner.json", d / "ops.history.log"


def _atomic_write(path, text):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_owner():
    _lock, owner, _h = _paths()
    try:
        return json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _try_lock(fh):
    if os.name == "nt":
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh):
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _busy_message():
    o = read_owner() or {}
    return f"另一个运维任务正在进行：{o.get('task', '未知')}（开始于 {o.get('started_at', '?')}，进程 {o.get('pid', '?')}）。请稍后重试。"


def current_token():
    return _current.get()


def inherited_token():
    """同一调用链已持锁（ContextVar），或父进程持锁并通过环境变量 ISH_OPS_TOKEN 传入时返回 token。"""
    t = _current.get()
    if t:
        return t
    tok = os.environ.get("ISH_OPS_TOKEN", "")
    if tok and tok not in _own_tokens:
        o = read_owner()
        if o and o.get("token") == tok:
            return tok
    return None


def child_env(extra=None):
    """调用子进程（manage.py、pg 工具）时使用：把当前运维 token 传给子进程。"""
    env = os.environ.copy()
    t = _current.get()
    if t:
        env["ISH_OPS_TOKEN"] = t
    if extra:
        env.update(extra)
    return env


@contextlib.contextmanager
def ops_lock(task: str, wait: float = 0):
    """获取全局运维锁；wait 秒内拿不到则抛 OpsBusy。返回 token。"""
    tok = inherited_token()
    if tok:
        reset = _current.set(tok)
        try:
            yield tok
        finally:
            _current.reset(reset)
        return
    lock, owner, hist = _paths()
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + max(0, wait)
    fh = None
    while True:
        try:
            fh = open(lock, "a+b")
        except PermissionError:  # PowerShell 以独占方式持有
            fh = None
        if fh is not None and _try_lock(fh):
            break
        if fh is not None:
            fh.close()
        if time.time() >= deadline:
            raise OpsBusy(_busy_message())
        time.sleep(0.5)
    token = secrets.token_hex(16)
    info = {"task": task, "token": token, "pid": os.getpid(), "host": socket.gethostname(), "started_at": datetime.now().isoformat(timespec="seconds")}
    prev = read_owner()
    _atomic_write(owner, json.dumps(info, ensure_ascii=False))
    with open(hist, "a", encoding="utf-8") as h:
        h.write(json.dumps({"event": "acquire", **{k: v for k, v in info.items() if k != "token"}, "previous": (prev or {}).get("task")}, ensure_ascii=False) + "\n")
    _own_tokens.add(token)
    reset = _current.set(token)
    try:
        yield token
    finally:
        _current.reset(reset)
        try:
            o = read_owner()
            if o and o.get("token") == token:
                _atomic_write(owner, json.dumps({**info, "token": "", "released_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))
            with open(hist, "a", encoding="utf-8") as h:
                h.write(json.dumps({"event": "release", "task": task, "pid": os.getpid(), "at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
        finally:
            _unlock(fh)
            fh.close()


def maintenance_owner():
    f = settings.MAINTENANCE_FLAG
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"token": None, "task": "legacy", "raw": f.read_text(encoding="utf-8", errors="replace")[:200]}


def set_maintenance(task: str, token: str = None):
    """放置维护标记；若已被其他 token 持有则抛 MaintenanceHeld。返回 (token, created)。

    为保持简单的调用方式，未指定 token 时返回新 token（created=True 的情形下仅返回 token）。
    """
    tok, _created = acquire_maintenance(task, token)
    return tok


def acquire_maintenance(task: str, token: str = None):
    cur = maintenance_owner()
    token = token or _current.get() or secrets.token_hex(16)
    if cur is not None:
        if cur.get("token") == token:
            return token, False
        raise MaintenanceHeld(f"系统处于维护状态（{cur.get('task')}，{cur.get('at', '')}）。可能是进行中的任务，或上次升级/恢复失败后保持的只读状态；请先运行 06-检查系统 并按手册处理。")
    settings.MAINTENANCE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(settings.MAINTENANCE_FLAG, json.dumps({"token": token, "task": task, "pid": os.getpid(), "at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))
    return token, True


def clear_maintenance(token: str) -> bool:
    """只删除自己（同 token）的维护标记。"""
    cur = maintenance_owner()
    if cur is None:
        return True
    if not token or cur.get("token") != token:
        return False
    settings.MAINTENANCE_FLAG.unlink(missing_ok=True)
    return True


def write_status_file(path, data: dict):
    """原子写入状态文件（每次使用唯一临时文件名，避免并发任务共用 tmp）。"""
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))
