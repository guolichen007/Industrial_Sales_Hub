"""对 acc7198 第四轮审查（2026-11-23）R77-P2-04：另一位销售（非负责人/创建人/成员）浏览全团队项目列表时，
can_edit 判断是否对每个项目额外查询数据库（N+1）。先测量 1/10/30/100 个项目的 SQL 数，再决定是否修。"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.projects.models import Project

pytestmark = pytest.mark.django_db


def _mk(n, owner, base, client_for, won=True):
    for i in range(n):
        p = Project.objects.create(code=f"PRJ-N{Project.objects.count():04d}", name=f"他人项目{i}", sales_owner=owner, created_by=owner)
        if won:
            Project.objects.filter(pk=p.pk).update(sales_locked_at="2026-11-20T10:00:00+08:00")


def _count(c, url):
    with CaptureQueriesContext(connection) as ctx:
        r = c.get(url)
    assert r.status_code == 200
    return len(ctx.captured_queries), len(r.json())


@pytest.mark.parametrize("url", ["/api/projects/?tracking=1", "/api/projects/"])
def test_R77_04_cross_team_list_query_count_constant(client_for, users, base, url):
    owner = users["sales"]  # 项目都属于销售 A；由销售 B（sales2）浏览
    c = client_for("sales2")
    counts = {}
    total = 0
    for n in (1, 10, 30, 100):
        _mk(n - total, owner, base, client_for)
        total = n
        q, rows = _count(c, url)
        counts[n] = q
    print("SQL_COUNTS", url, counts)
    assert counts[100] - counts[1] <= 2, counts  # 查询数不随项目数线性增长


def test_R77_04_cross_team_presale_list_query_count_constant(client_for, users, base):
    """未中标（售前）项目：can_edit 不能被 sales_locked_at 短路，需走成员判断。"""
    owner = users["sales"]
    c = client_for("sales2")
    counts, total = {}, 0
    for n in (1, 10, 30, 100):
        _mk(n - total, owner, base, client_for, won=False)
        total = n
        counts[n] = _count(c, "/api/projects/")[0]
    print("SQL_COUNTS presale", counts)
    assert counts[100] - counts[1] <= 2, counts


def test_R77_04_member_sales_still_editable_via_prefetch(client_for, users, base):
    """优化后语义不变：成员销售在列表中 can_edit=True，非成员 False；中标后均 False。"""
    p = base["p_other"]
    p.members.add(users["sales2"])
    rows = {x["id"]: x for x in client_for("sales2").get("/api/projects/").json()}
    assert rows[p.id]["can_edit"] is True and rows[base["p_sales"].id]["can_edit"] is False
    assert client_for("sales2").patch(f"/api/projects/{p.id}/", {"location": "成员可改"}, format="json").status_code == 200
    client_for("assistant").post(f"/api/projects/{p.id}/handoff/", {}, format="json")
    rows = {x["id"]: x for x in client_for("sales2").get("/api/projects/").json()}
    assert rows[p.id]["can_edit"] is False
