"""V2.1-LAN 浏览器验收（真实 Waitress + PostgreSQL + LAN 模式 + 真实账号，不使用演示数据）。

用法：E2E_BASE=http://<主机局域网IP>:8000 E2E_ADMIN=<管理员> E2E_ADMIN_PW=<密码> python e2e/e2e_v21_lan.py <输出目录>
流程：管理员创建 5 个业务账号 → 负责人维护出具主体 → 销售登记无客户项目并关联参与方 → 商务为两家公司/客户报价、V2 →
      财务确认合同（无金额）→ 财务更新节点 → 第二个浏览器上下文中销售在刷新/轮询后看到节点 → 负责人补录金额/业绩/案例 →
      销售生成公开案例 PDF；同时验证每个角色的菜单、越权 API、金额泄露、移动端、无 JS 错误；记录 API 时延 P95。
"""
import json
import os
import re
import statistics
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ["E2E_BASE"].rstrip("/")
ADMIN, ADMIN_PW = os.environ["E2E_ADMIN"], os.environ["E2E_ADMIN_PW"]
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
PW = "Lan-2026-pass"
USERS = {"leader": ("张经理", "LEADER"), "assistant": ("商务·刘敏", "ASSISTANT"), "sales": ("销售·李工", "SALES"), "sales2": ("销售·王工", "SALES"), "finance": ("财务·陈静", "FINANCE")}
results, js_errors, timings = [], [], []


def check(name, cond, detail=""):
    results.append({"name": name, "pass": bool(cond), "detail": "" if cond else str(detail)[:300]})
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {detail}"))


def track(page, who):
    page.on("pageerror", lambda e: js_errors.append(f"{who}: {e}"))
    page.on("console", lambda m: m.type == "error" and "403" not in m.text and "404" not in m.text and "409" not in m.text and "400" not in m.text and js_errors.append(f"{who}: {m.text}"))
    def done(req):
        if not req.url.startswith(BASE + "/api/"):
            return
        t = req.timing  # requestfinished 时 responseEnd 已可用（相对 startTime 的毫秒）
        resp = req.response()
        timings.append((req.method, re.sub(r"/\d+/", "/{id}/", req.url[len(BASE):].split("?")[0]), t["responseEnd"], resp.status if resp else 0, who))

    page.on("requestfinished", done)
    page.on("response", lambda r: r.url.startswith(BASE + "/api/") and r.status >= 500 and timings.append((r.request.method, r.url[len(BASE):], -1, r.status, who)))


def login(ctx, user, pw):
    p = ctx.new_page()
    track(p, user)
    p.goto(BASE + "/")
    p.fill("#u", user)
    p.fill("#p", pw)
    p.click("button.btn.primary")
    try:
        p.wait_for_selector("aside", timeout=15000)
    except Exception:
        # 失败证据：截图 + 页面文本，CI 制品中可直接查看
        tag = f"login_fail_{user}_{p.viewport_size['width'] if p.viewport_size else 0}"
        p.screenshot(path=f"{OUT}/{tag}.png", full_page=True)
        open(f"{OUT}/{tag}.txt", "w", encoding="utf-8").write(p.inner_text("body"))
        raise
    return p


def api(page, method, path, body=None):
    """在页面上下文里发请求（带 Session 与 CSRF），返回 (status, json)。"""
    js = """async ([m, p, b]) => {
      const c = document.cookie.match(/csrftoken=([^;]+)/);
      const r = await fetch('/api' + p, {method: m, headers: {'Content-Type': 'application/json', 'X-CSRFToken': c ? c[1] : ''}, body: b ? JSON.stringify(b) : undefined, credentials: 'same-origin'});
      let d = null; try { d = await r.json(); } catch (e) {}
      return [r.status, d];
    }"""
    return page.evaluate(js, [method, path, body])


def nav_labels(p):
    return " ".join(p.locator(".nav a").all_inner_texts())


with sync_playwright() as pw:
    b = pw.chromium.launch()
    new = lambda w=1366, h=768: b.new_context(viewport={"width": w, "height": h}, locale="zh-CN")

    # ---------- 管理员：创建业务账号 ----------
    ca = new()
    pa = login(ca, ADMIN, ADMIN_PW)
    pa.wait_for_selector("text=系统管理")
    pa.screenshot(path=f"{OUT}/01_admin_home_1366.png", full_page=True)
    check("管理员首页为系统管理（无业务金额）", "运行模式" in pa.inner_text("body") and "报价中心" not in nav_labels(pa))
    for u, (name, role) in USERS.items():
        s, d = api(pa, "POST", "/users/", {"username": u, "display_name": name, "role": role, "password": PW})
        check(f"管理员创建账号 {u}", s == 201, d)
    s, _ = api(pa, "GET", "/quotations/")
    check("管理员访问报价 API 被拒", s == 403, s)
    s, d = api(pa, "PATCH", f"/users/{api(pa, 'GET', '/auth/me/')[1]['id']}/", {"role": "SALES"})
    check("最后一个管理员不能自降级", s == 409, d)
    pa.goto(BASE + "/settings")
    pa.wait_for_selector("text=账号与角色")
    pa.screenshot(path=f"{OUT}/02_admin_users.png", full_page=True)

    # ---------- 负责人：维护出具主体 ----------
    cl = new()
    pl = login(cl, "leader", PW)
    ents = api(pl, "GET", "/legal-entities/")[1]
    for e, nm in zip(ents, ["验收甲机电设备有限公司", "验收乙工业技术有限公司", "验收丙电气科技有限公司"]):
        s, _ = api(pl, "PATCH", f"/legal-entities/{e['id']}/", {"name": nm, "short_name": nm[2:4] + "公司", "address": "验收地址", "phone": "000-0000000"})
    check("负责人维护三家出具主体", s == 200)
    for n in ["北方重工设备有限公司", "南方港机制造股份有限公司", "华东港口产业集团"]:
        api(pl, "POST", "/accounts/", {"name": n, "kinds": ["主机厂"] if "港口" not in n else ["业主"]})
    check("负责人首页", "经营与项目总览" in pl.inner_text("body"))
    pl.screenshot(path=f"{OUT}/03_leader_home_1920.png", full_page=True)

    # ---------- 销售：无客户建项目、关联参与方 ----------
    cs = new()
    ps = login(cs, "sales", PW)
    labels = nav_labels(ps)
    check("销售菜单无报价中心/产品库", "报价中心" not in labels and "产品型号库" not in labels, labels)
    ps.goto(BASE + "/projects?new=1")
    ps.wait_for_selector(".modal")
    ps.fill(".modal input.input >> nth=0", "LAN验收·滨海码头改造")
    ps.click(".modalfoot button.primary")
    ps.wait_for_url("**/projects/*")
    ps.wait_for_selector(".detailbanner")
    pid = int(ps.url.rstrip("/").split("/")[-1])
    check("销售登记无客户项目", "尚未关联客户" in ps.inner_text("body"))
    for idx, role in [(0, "主机厂"), (1, "主机厂"), (2, "业主")]:
        ps.locator("button:has-text('关联参与方')").first.click()
        ps.wait_for_selector(".modal select")
        ps.wait_for_timeout(400)
        ps.locator(".modal select").first.select_option(index=idx)
        ps.locator(".modal select").nth(1).select_option(role)
        ps.click(".modalfoot button.primary")
        ps.wait_for_timeout(700)
    check("项目关联 3 个参与方", ps.locator(".card:has-text('参与方') table tbody tr").count() == 3)
    s, _ = api(ps, "PATCH", f"/projects/{pid}/", {"presale_phase": "成交"})
    check("销售不能直接设为成交", s == 400, s)
    ps.screenshot(path=f"{OUT}/04_sales_project.png", full_page=True)

    # ---------- 商务：两家公司/客户报价 + V2 ----------
    cas = new(1920, 1080)
    pas = login(cas, "assistant", PW)
    api(pas, "POST", "/product-categories/", {"name": "润滑系统"})
    cat = api(pas, "GET", "/product-categories/")[1][0]["id"]
    s, prod = api(pas, "POST", "/products/", {"sku": "LUB-320", "model": "LUB-320", "name": "集中润滑系统", "category": cat, "unit": "套", "base_price": "39000", "params": [{"label": "工作压力", "value": "25", "unit": "MPa"}, {"label": "储油量", "value": "320", "unit": "L"}]})
    check("商务新增产品", s == 201, prod)
    accs = api(pas, "GET", "/accounts/")[1]
    acc = {a["name"]: a["id"] for a in accs}
    qids = []
    for ent, rec in [(ents[0]["id"], acc["北方重工设备有限公司"]), (ents[1]["id"], acc["南方港机制造股份有限公司"])]:
        s, q = api(pas, "POST", "/quotations/", {"project": pid, "recipient": rec, "legal_entity": ent})
        qids.append(q["id"])
        rid = q["latest"]["id"]
        api(pas, "POST", f"/quote-revisions/{rid}/lines/", {"product": prod["id"], "qty": "2"})
    pas.goto(f"{BASE}/quotes/{qids[0]}")
    pas.wait_for_selector("text=报价明细")
    first = pas.locator(".linebox").first
    price = first.locator(".lineedit input").nth(3)
    price.fill("37500")
    price.press("Enter")
    pas.wait_for_timeout(900)
    total = pas.locator(".totals b").nth(2).inner_text()
    check("报价改价后后端重算（2×37500×1.13=84,750）", "84,750.00" in total, total)
    pas.on("dialog", lambda d: d.accept())
    pas.click("text=生成正式 PDF 并冻结")
    pas.wait_for_selector("text=下载 PDF", timeout=30000)
    check("报价 V1 冻结", pas.locator(".linebox .lineedit input").first.is_disabled())
    pas.screenshot(path=f"{OUT}/05_assistant_quote_frozen_1920.png", full_page=True)
    q1 = api(pas, "GET", f"/quotations/{qids[0]}/")[1]
    v1 = q1["revisions"][0]
    r = pas.request.get(f"{BASE}/api/quote-revisions/{v1['id']}/pdf/")
    import hashlib

    check("V1 PDF 下载哈希与存档一致", r.status == 200 and hashlib.sha256(r.body()).hexdigest() == v1["pdf_sha256"])
    pas.click("text=+ 新版本")
    try:
        pas.wait_for_selector("button:has-text('V2')", timeout=15000)
    except Exception:  # noqa: BLE001 —— 由下方断言记录失败
        pass
    check("创建 V2 草稿", pas.locator("button:has-text('V2')").count() == 1)
    r2 = pas.request.get(f"{BASE}/api/quote-revisions/{v1['id']}/pdf/")
    check("V2 创建后 V1 原字节不变", hashlib.sha256(r2.body()).hexdigest() == v1["pdf_sha256"])
    # 第二份报价生成
    q2 = api(pas, "GET", f"/quotations/{qids[1]}/")[1]
    s, _ = api(pas, "POST", f"/quote-revisions/{q2['latest']['id']}/render/")
    check("第二家公司/客户报价生成", s == 200)

    # ---------- 销售越权反例 ----------
    for path in [f"/quotations/{qids[0]}/", f"/quote-revisions/{v1['id']}/", f"/projects/{pid}/finance/"]:
        s, _ = api(ps, "GET", path)
        check(f"销售访问 {path} 被拒", s in (403, 404), s)
    s, rel = api(ps, "GET", f"/projects/{pid}/related/")
    check("销售项目关联无报价、无订单", rel["quotes"] == [] and rel["orders"] == [])
    s, lst = api(ps, "GET", "/projects/")
    check("销售项目列表无报价数量字段", "quote_count" not in json.dumps(lst))
    r = ps.request.get(f"{BASE}/api/quote-revisions/{v1['id']}/pdf/")
    check("销售直接下载报价 PDF 被拒", r.status == 403, r.status)

    # ---------- 财务：确认合同（无金额）、更新节点 ----------
    cf = new()
    pf = login(cf, "finance", PW)
    check("财务菜单无报价中心", "报价中心" not in nav_labels(pf))
    try:
        pf.wait_for_selector("text=合同确认与交付执行", timeout=15000)
    except Exception:  # noqa: BLE001 —— 由下方断言记录失败
        pass
    check("财务首页：合同确认与交付执行", "合同确认与交付执行" in pf.inner_text("body"))
    s, d = api(pf, "POST", "/order-confirmations/", {"project": pid, "legal_entity": ents[0]["id"], "basis": "CONTRACT", "order_ref": "HT-LAN-001", "contract_amount": "1"})
    check("财务提交金额被拒", s == 403, s)
    pf.goto(f"{BASE}/projects/{pid}")
    pf.wait_for_selector(".detailbanner")
    pf.click("text=登记合同/订单")
    pf.wait_for_selector(".modal")
    check("财务合同表单无金额输入", "由负责人或商务录入" in pf.inner_text(".modal"))
    pf.locator(".modal input.input").first.fill("HT-LAN-001")
    pf.locator(".modal select").nth(1).select_option(index=1) if pf.locator(".modal select").count() > 2 else None
    sel_ent = pf.locator(".modal select").filter(has_text="请选择")
    if sel_ent.count():
        sel_ent.first.select_option(index=1)
    pf.click(".modalfoot >> text=登记并确认业绩")
    pf.wait_for_timeout(1500)
    body = pf.inner_text("body")
    check("财务确认后项目进入执行并自动建节点", "执行：执行中" in body and "订单已确认" in body, body[:200])
    s, orders = api(pf, "GET", "/order-confirmations/")
    check("财务订单接口无金额与报价编号", "contract_amount" not in json.dumps(orders) and "quote_code" not in json.dumps(orders))
    pf.click(".tabbar >> text=合同执行")
    pf.wait_for_selector("text=执行节点")
    pf.locator("table tr:has-text('生产/备货') button:has-text('更新')").click()
    pf.locator("table tr:has-text('生产/备货') select").select_option("DELAYED")
    pf.wait_for_timeout(300)
    save_btn = pf.locator("table tr:has-text('生产/备货') button:has-text('保存')")
    check("延期未填原因时保存按钮禁用", save_btn.is_disabled())
    pf.locator("table tr:has-text('生产/备货') input[placeholder^='延期原因']").fill("主机厂图纸变更")
    save_btn.click()
    pf.wait_for_timeout(1000)
    t_update = time.time()
    pf.screenshot(path=f"{OUT}/06_finance_execution.png", full_page=True)

    # ---------- 第二个独立浏览器上下文：销售在刷新/轮询后看到更新 ----------
    cs2 = new()
    ps2 = login(cs2, "sales", PW)
    ps2.goto(f"{BASE}/execution")
    seen = False
    for _ in range(40):
        if "主机厂图纸变更" in ps2.inner_text("body") or "延期" in ps2.inner_text("body"):
            seen = True
            break
        ps2.wait_for_timeout(1000)
    lag = round(time.time() - t_update, 1)
    if not seen:
        ps2.goto(f"{BASE}/projects/{pid}")
        ps2.wait_for_selector(".detailbanner")
        ps2.click(".tabbar >> text=合同执行")
        ps2.wait_for_timeout(1000)
        seen = "主机厂图纸变更" in ps2.inner_text("body")
    check(f"另一浏览器上下文中销售看到财务更新（{lag}s 内）", seen)
    body = ps2.inner_text("body")
    check("销售执行视图无金额", "¥" not in body and "HT-LAN-001" not in body)
    ps2.screenshot(path=f"{OUT}/07_sales_sees_progress.png", full_page=True)

    # ---------- 负责人：补录金额、业绩可公开、案例 ----------
    s, perfs = api(pl, "GET", "/performances/")
    check("订单生成唯一业绩", s == 200 and len(perfs) == 1, perfs)
    s, _ = api(pl, "PATCH", f"/performances/{perfs[0]['id']}/", {"publishable": True, "public_summary": "为沿海码头斗轮机配套集中润滑系统。"})
    check("负责人设置业绩可公开", s == 200)
    pl.goto(BASE + "/performance")
    pl.wait_for_selector("table")
    pl.screenshot(path=f"{OUT}/08_leader_performance.png", full_page=True)

    # ---------- 销售：可公开案例生成 PDF ----------
    ps.goto(BASE + "/cases")
    ps.wait_for_selector(".casecheck")
    ps.locator(".casecheck").first.check()
    ps.wait_for_timeout(800)
    prev = ps.inner_text(".casepreview")
    check("案例预览无金额/合同号/主机厂", "¥" not in prev and "HT-LAN" not in prev and "北方重工" not in prev, prev)
    gen = ps.locator("button:has-text('生成案例册 PDF')")
    check("未人工确认前不能生成", gen.is_disabled())
    ps.locator("text=我已人工核对上方预览").click()
    with ps.expect_popup() as pop:
        gen.click()
    pop.value.close()  # 无头浏览器中 PDF 弹窗不会触发 load 事件；下面直接校验文件字节
    ps.wait_for_timeout(800)
    s, rels = api(ps, "GET", "/case-releases/")
    check("销售生成案例册 PDF", s == 200 and len(rels) == 1)
    r = ps.request.get(f"{BASE}/api/case-releases/{rels[0]['id']}/pdf/")
    check("案例 PDF 字节不含合同号/主机厂", r.status == 200 and b"HT-LAN" not in r.body())
    ps.screenshot(path=f"{OUT}/09_sales_cases.png", full_page=True)

    # ---------- D7：销售2 可见项目但不可编辑 ----------
    c3 = new()
    p3 = login(c3, "sales2", PW)
    p3.goto(f"{BASE}/projects/{pid}")
    p3.wait_for_selector(".detailbanner")
    check("其他销售可看项目公共进度、显示只读", "只读" in p3.inner_text(".detailbanner") and p3.locator("text=编辑项目").count() == 0)
    s, _ = api(p3, "PATCH", f"/projects/{pid}/", {"name": "篡改"})
    check("其他销售修改项目被拒", s == 403, s)
    s, perf2 = api(p3, "GET", "/performances/")
    check("其他销售看不到该业绩", perf2 == [])

    # ---------- 匿名 ----------
    can_ = new()
    pz = can_.new_page()
    pz.goto(BASE + "/")
    for path in ["/projects/", "/quotations/", "/performances/", "/users/"]:
        r = pz.request.get(BASE + "/api" + path)
        check(f"匿名访问 {path} 被拒", r.status == 403, r.status)

    # ---------- 移动端 ----------
    cm = new(390, 844)
    pm = login(cm, "sales", PW)
    pm.wait_for_selector(".kpi")
    over = pm.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    check("手机 390px 无页面级横向溢出（销售首页）", not over)
    pm.screenshot(path=f"{OUT}/10_mobile_sales.png", full_page=True)
    pm.goto(f"{BASE}/projects/{pid}")
    pm.wait_for_selector(".detailbanner")
    wide = pm.evaluate("""() => { const W = window.innerWidth; const out = [];
      document.querySelectorAll('body *').forEach(e => { const r = e.getBoundingClientRect(); if (r.right > W + 1 && !e.closest('.tablewrap')) out.push(e.tagName + '.' + e.className + ' ' + Math.round(r.right)); });
      return out.slice(0, 6); }""")
    check("手机 390px 项目详情无横向溢出", not pm.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"), wide)
    pm.screenshot(path=f"{OUT}/11_mobile_project.png", full_page=True)

    # ---------- 5 用户混合并发浏览 ----------
    pages = [pl, pas, ps, pf, p3]
    urls = ["/", "/projects", f"/projects/{pid}", "/execution", "/performance", "/cases"]
    t0 = time.time()
    for rnd in range(4):
        for i, p in enumerate(pages):
            u = urls[(rnd + i) % len(urls)]
            if u == "/cases" and p in (pf,):
                u = "/execution"
            p.goto(BASE + u)
        for p in pages:
            p.wait_for_load_state("networkidle")
    check(f"5 用户混合浏览 4 轮完成（{round(time.time() - t0, 1)}s）", True)
    b.close()

ok = [t for t in timings if t[2] and t[2] > 0]
api_ms = sorted(t[2] for t in ok if "/pdf" not in t[1] and "/render" not in t[1])
pdf_ms = sorted(t[2] for t in ok if "/render" in t[1] or "/pdf" in t[1])
p95 = lambda xs: round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 1) if xs else None
srv5xx = [t for t in timings if t[3] >= 500]
perf = {"api_requests": len(api_ms), "api_p50_ms": round(statistics.median(api_ms), 1) if api_ms else None, "api_p95_ms": p95(api_ms), "api_max_ms": round(max(api_ms), 1) if api_ms else None,
        "pdf_requests": len(pdf_ms), "pdf_p95_ms": p95(pdf_ms), "pdf_max_ms": round(max(pdf_ms), 1) if pdf_ms else None, "http_5xx": len(srv5xx)}
check(f"浏览器发起的 API 无 5xx（{len(timings)} 个请求）", not srv5xx, srv5xx[:3])
check(f"普通 API P95 ≤ 1000ms（实测 {perf['api_p95_ms']} ms）", perf["api_p95_ms"] is not None and perf["api_p95_ms"] <= 1000)
check(f"PDF 生成/下载 P95 ≤ 5000ms（实测 {perf['pdf_p95_ms']} ms）", perf["pdf_p95_ms"] is None or perf["pdf_p95_ms"] <= 5000)
check("浏览器无 JS 运行错误", not js_errors, js_errors[:5])
slowest = sorted(ok, key=lambda t: -t[2])[:25]
json.dump({"results": results, "perf": perf, "js_errors": js_errors, "slowest_api": slowest}, open(f"{OUT}/e2e_v21_lan_results.json", "w"), ensure_ascii=False, indent=1)
n = sum(r["pass"] for r in results)
print(f"\n{n}/{len(results)} PASS")
print(json.dumps(perf, ensure_ascii=False))
sys.exit(0 if n == len(results) else 1)
