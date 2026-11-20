"""RC2-R01：安装重复执行不得覆盖已有出具主体与模板签收。

反例（RC1）：install.ps1 每次调用 seed_entities，已有主体按 JSON 逐字段覆盖（名称被改回占位、签收被重置为 false）。
修复后：
- seed_entities 默认 create-only：只创建缺失主体，已有主体一个字段都不改；
- 覆盖更新必须用独立命令 import_entity_config，且必须指定一个启用的“部门负责人”账号作为授权人，并写审计；
- import_entity_config 永远不修改 template_verified（签收只能由负责人在系统内按模板版本完成）。
"""
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.audit.models import AuditEvent
from apps.organizations.models import LegalEntity

DEFAULTS = [
    {"code": "A", "short_name": "公司A（待填）", "name": "【占位A】", "template_key": "A", "price_basis": "NET", "default_terms": "默认条款"},
    {"code": "B", "short_name": "公司B（待填）", "name": "【占位B】", "template_key": "B", "price_basis": "NET", "default_terms": "默认条款"},
]


@pytest.fixture
def entities_json(tmp_path):
    p = tmp_path / "entities.json"
    p.write_text(json.dumps(DEFAULTS, ensure_ascii=False), encoding="utf-8")
    return p


def _customize():
    e = LegalEntity.objects.get(code="A")
    e.name = "真实甲方主体全称有限公司"
    e.short_name = "甲方"
    e.price_basis = "GROSS"
    e.default_terms = "负责人改过的条款"
    e.template_verified = True
    e.save()
    return e


@pytest.mark.django_db
def test_seed_entities_is_create_only(entities_json):
    call_command("seed_entities", str(entities_json))
    assert LegalEntity.objects.count() == 2
    before = _customize()
    snapshot = {f.name: getattr(before, f.name) for f in LegalEntity._meta.concrete_fields}
    # 第二次“安装”：不得改变已有主体的任何字段
    call_command("seed_entities", str(entities_json))
    after = LegalEntity.objects.get(code="A")
    assert {f.name: getattr(after, f.name) for f in LegalEntity._meta.concrete_fields} == snapshot
    # 缺失的主体仍会创建
    LegalEntity.objects.filter(code="B").delete()
    call_command("seed_entities", str(entities_json))
    assert LegalEntity.objects.filter(code="B").exists()


@pytest.mark.django_db
def test_seed_entities_rejects_verified_flag_in_json(tmp_path):
    p = tmp_path / "e.json"
    p.write_text(json.dumps([{**DEFAULTS[0], "template_verified": True}], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CommandError, match="签收"):
        call_command("seed_entities", str(p))
    assert not LegalEntity.objects.exists()


@pytest.mark.django_db
def test_import_entity_config_requires_leader(users, entities_json):
    call_command("seed_entities", str(entities_json))
    _customize()
    for who in ["", "admin", "assistant", "sales", "nobody"]:
        with pytest.raises(CommandError):
            call_command("import_entity_config", str(entities_json), authorized_by=who)
    assert LegalEntity.objects.get(code="A").name == "真实甲方主体全称有限公司"


@pytest.mark.django_db
def test_import_entity_config_updates_but_never_touches_verification(users, tmp_path, entities_json):
    call_command("seed_entities", str(entities_json))
    _customize()
    p = tmp_path / "real.json"
    p.write_text(json.dumps([{"code": "A", "name": "导入的新全称", "default_terms": "导入条款"}], ensure_ascii=False), encoding="utf-8")
    call_command("import_entity_config", str(p), authorized_by="leader")
    e = LegalEntity.objects.get(code="A")
    assert e.name == "导入的新全称" and e.default_terms == "导入条款"
    assert e.price_basis == "GROSS"  # 未出现在 JSON 中的字段保持不变
    assert e.template_verified is True  # 导入不改签收
    ev = AuditEvent.objects.filter(resource="legal_entity", action="import_config").latest("id")
    assert ev.actor.username == "leader" and "A" in ev.summary
    # JSON 中带 template_verified 一律拒绝（无论 true/false）
    p.write_text(json.dumps([{"code": "A", "template_verified": False}]), encoding="utf-8")
    with pytest.raises(CommandError, match="签收"):
        call_command("import_entity_config", str(p), authorized_by="leader")
    assert LegalEntity.objects.get(code="A").template_verified is True


@pytest.mark.django_db
def test_import_entity_config_dry_run_changes_nothing(users, tmp_path, entities_json):
    call_command("seed_entities", str(entities_json))
    p = tmp_path / "real.json"
    p.write_text(json.dumps([{"code": "A", "name": "预览名"}], ensure_ascii=False), encoding="utf-8")
    call_command("import_entity_config", str(p), authorized_by="leader", dry_run=True)
    assert LegalEntity.objects.get(code="A").name == "【占位A】"


def _db_fingerprint():
    """所有业务表逐行内容的哈希（不含 last_login 等登录字段），用于“重复安装不改数据”的对比。"""
    import hashlib

    from django.apps import apps
    from django.core import serializers as djs

    h = {}
    for m in apps.get_models():
        if m._meta.app_label in ("accounts", "organizations", "products", "projects", "quotations", "contracts", "performances", "files", "audit", "imports"):
            data = djs.serialize("json", m.objects.order_by("pk"))
            h[m._meta.label] = hashlib.sha256(data.encode()).hexdigest()
    return h


def _files_fingerprint(root):
    import hashlib

    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.django_db
def test_repeated_install_sequence_changes_nothing(users, base, make_quote, entities_json, private_dir, monkeypatch):
    """模拟 install.ps1 的数据库相关步骤执行两次：migrate → seed_entities → init_admin --check。
    第二次前后：账号、主体（含签收）、报价、PDF/附件哈希全部不变。"""
    make_quote()
    for e in LegalEntity.objects.all():
        e.template_verified = True
        e.save()
    call_command("seed_entities", str(entities_json))
    db1, f1 = _db_fingerprint(), _files_fingerprint(private_dir)
    assert f1, "应至少有一个报价 PDF"
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_entities", str(entities_json))
    call_command("init_admin", check=True)  # 已有管理员：只检查、不创建、不改密码
    assert _db_fingerprint() == db1
    assert _files_fingerprint(private_dir) == f1
