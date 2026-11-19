import os, sys, time, json
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8765")
SHOT = sys.argv[1]
os.makedirs(SHOT, exist_ok=True)
errors = []
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))

with sync_playwright() as p:
    b = p.chromium.launch()
    def ctx(w=1440, h=900):
        c = b.new_context(viewport={"width": w, "height": h}, locale="zh-CN")
        pg = c.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        return c, pg
    def login(pg, u):
        pg.goto(BASE + "/")
        pg.fill("#u", u); pg.fill("#p", "Demo@2026"); pg.click("button.btn.primary")
        pg.wait_for_selector("aside")

    # ---- 商务助理：完整报价流程 ----
    c, pg = ctx(); login(pg, "assistant")
    pg.wait_for_selector(".kpi"); pg.screenshot(path=f"{SHOT}/01_assistant_dashboard.png")
    navs = pg.locator(".nav a").all_inner_texts()
    check("商务助理可见报价中心与产品库", "报价中心" in " ".join(navs) and "产品型号库" in " ".join(navs))
    pg.goto(BASE + "/quotes?new=1"); pg.wait_for_selector(".modal")
    pg.locator(".choice").nth(1).click()
    pg.locator(".modal select").nth(0).select_option(index=1)
    pg.wait_for_timeout(600)
    pg.locator(".modal select").nth(1).select_option(index=1)
    pg.wait_for_timeout(300)
    pg.locator(".modal select").nth(2).select_option(index=1) if pg.locator(".modal select").nth(2).locator("option").count() > 1 else None
    pg.screenshot(path=f"{SHOT}/02_new_quote_step1.png")
    pg.click("text=创建并编辑明细 →"); pg.wait_for_url("**/quotes/*")
    pg.wait_for_selector("text=报价明细")
    for i in range(2):
        pg.click("text=+ 选择产品"); pg.wait_for_selector(".modal table")
        pg.locator(".modal button:has-text('加入')").nth(i).click()
        pg.wait_for_timeout(500)
    # 修改数量和单价
    first = pg.locator(".linebox").first
    qty = first.locator(".lineedit input").nth(0); qty.fill("3"); qty.press("Enter"); pg.wait_for_timeout(600)
    price = first.locator(".lineedit input").nth(2); price.fill("37500"); price.press("Enter"); pg.wait_for_timeout(700)
    # 取消显示一个参数
    first.locator(".quote-model-params input").first.click(); pg.wait_for_timeout(900)
    check("取消显示参数后状态由服务端回写", not first.locator(".quote-model-params input").first.is_checked())
    total = pg.locator(".totals b").nth(2).inner_text()
    # 3*37500=112500 → 127125.00 ; 18200*1 → 20566.00 ; total 147691.00
    check("报价合计由后端 Decimal 计算", "147,691.00" in total, total)
    pg.screenshot(path=f"{SHOT}/03_quote_editor.png", full_page=True)
    pg.on("dialog", lambda d: d.accept())
    pg.click("text=生成正式 PDF 并冻结"); pg.wait_for_selector("text=下载 PDF", timeout=20000)
    check("生成后版本冻结（输入框禁用）", pg.locator(".linebox .lineedit input").first.is_disabled())
    check("显示 PDF 与快照哈希", "PDF SHA-256" in pg.inner_text(".notice"))
    pg.screenshot(path=f"{SHOT}/04_quote_frozen.png", full_page=True)
    pdf = pg.request.get(pg.url.replace("/quotes/", "/api/quotations/").rstrip("/") + "/").json()
    rid = pdf["revisions"][0]["id"]
    r = pg.request.get(f"{BASE}/api/quote-revisions/{rid}/pdf/")
    check("PDF 下载", r.status == 200 and r.body()[:4] == b"%PDF")
    open(f"{SHOT}/quote_V1.pdf", "wb").write(r.body())
    pg.click("text=+ 新版本"); pg.wait_for_timeout(800)
    check("创建 V2 草稿", pg.locator("button:has-text('V2')").count() == 1 and not pg.locator(".linebox .lineedit input").first.is_disabled())
    quote_url = pg.url
    c.close()

    # ---- 销售：建项目、无报价入口、无金额 ----
    c, pg = ctx(); login(pg, "sales")
    navs = " ".join(pg.locator(".nav a").all_inner_texts())
    check("销售看不到报价中心/产品库菜单", "报价中心" not in navs and "产品型号库" not in navs)
    pg.screenshot(path=f"{SHOT}/05_sales_dashboard.png")
    pg.goto(BASE + "/projects?new=1"); pg.wait_for_selector(".modal")
    pg.fill(".modal input.input >> nth=0", "E2E 无客户新项目")
    pg.click(".modalfoot button.primary"); pg.wait_for_url("**/projects/*")
    pg.wait_for_selector(".detailbanner")
    check("销售可建无客户项目", "尚未关联客户" in pg.inner_text("body"))
    pg.screenshot(path=f"{SHOT}/debug_party.png", full_page=True)
    try:
        pg.locator("button:has-text('关联参与方')").first.click(timeout=5000)
    except Exception as e:
        print("CLICKERR", str(e)[-1500:]); raise
    pg.wait_for_selector(".modal select")
    pg.wait_for_timeout(500)
    pg.locator(".modal select").first.select_option(index=0)
    pg.click(".modalfoot button.primary"); pg.wait_for_timeout(800)
    pg.locator("button:has-text('关联参与方')").first.click(); pg.wait_for_selector(".modal select")
    pg.wait_for_timeout(500)
    pg.locator(".modal select").first.select_option(index=1)
    pg.locator(".modal select").nth(1).select_option("投资方")
    pg.click(".modalfoot button.primary"); pg.wait_for_timeout(800)
    check("项目关联多个参与方", pg.locator(".card:has-text('参与方') table tbody tr").count() == 2)
    pg.click("text=+ 售前跟进"); pg.wait_for_selector(".modal")
    pg.fill(".modal input.input >> nth=0", "首次拜访"); pg.click(".modalfoot button.primary"); pg.wait_for_timeout(700)
    check("销售无执行进展按钮", pg.locator("text=+ 执行进展").count() == 0)
    pg.screenshot(path=f"{SHOT}/06_sales_project_detail.png", full_page=True)
    r = pg.request.get(f"{BASE}/api/quotations/")
    check("销售直接调用报价 API 被拒(403)", r.status == 403, str(r.status))
    pg.goto(quote_url); pg.wait_for_timeout(600)
    check("销售直接访问报价页显示无权", "无权访问" in pg.inner_text("body"))
    c.close()

    # ---- 负责人：登记订单 → 业绩 → 执行节点 ----
    c, pg = ctx(); login(pg, "leader")
    pg.goto(BASE + "/projects"); pg.click("text=滨海煤码头堆取料机改造"); pg.wait_for_selector(".detailbanner")
    pg.click("text=登记合同/订单"); pg.wait_for_selector(".modal")
    pg.locator(".modal select").nth(1).select_option(index=1)
    pg.locator(".modal input.input").nth(0).fill("HT-E2E-001")
    pg.click(".modal >> text=登记并确认业绩"); pg.wait_for_timeout(1500)
    body = pg.inner_text("body")
    check("订单确认后项目进入执行并生成节点", "执行：执行中" in body and "订单已确认" in body)
    pg.screenshot(path=f"{SHOT}/07_leader_order_confirmed.png", full_page=True)
    pg.goto(BASE + "/performance"); pg.wait_for_selector("table")
    check("业绩台账出现订单业绩", "订单确认" in pg.inner_text("table"))
    pg.locator("tr:has-text('滨海煤码头') >> text=编辑").click(); pg.wait_for_selector(".modal")
    pg.locator(".modal textarea").first.fill("为沿海码头斗轮机配套集中润滑与安全制动设备。")
    pg.locator(".modal >> text=允许作为对外案例").click()
    pg.click(".modalfoot button.primary"); pg.wait_for_timeout(800)
    pg.screenshot(path=f"{SHOT}/08_performance_ledger.png", full_page=True)
    pg.goto(BASE + "/cases"); pg.wait_for_selector(".casecheck")
    pg.locator(".casecheck").first.check(); pg.wait_for_timeout(800)
    prev = pg.inner_text(".casepreview")
    check("案例预览不含金额", "¥" not in prev and "HT-E2E" not in prev, prev[:80])
    with pg.expect_popup() as pop:
        pg.click("text=生成案例册 PDF")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{SHOT}/09_cases.png", full_page=True)
    c.close()

    # ---- 财务：更新执行节点；销售查看 ----
    c, pg = ctx(); login(pg, "finance")
    navs = " ".join(pg.locator(".nav a").all_inner_texts())
    check("财务无报价中心", "报价中心" not in navs)
    pg.goto(BASE + "/projects"); pg.click("text=滨海煤码头堆取料机改造"); pg.wait_for_selector(".detailbanner")
    pg.click(".tabbar >> text=合同执行"); pg.wait_for_selector("text=执行节点")
    pg.locator("table tr:has-text('技术资料确认') button:has-text('更新')").click()
    pg.locator("tr:has-text('技术资料确认') select").select_option("DONE")
    pg.locator("table tr:has-text('技术资料确认') button:has-text('保存')").click(); pg.wait_for_timeout(900)
    pg.locator("table tr:has-text('生产/备货') button:has-text('更新')").click()
    pg.locator("tr:has-text('生产/备货') select").select_option("IN_PROGRESS")
    pg.locator("table tr:has-text('生产/备货') button:has-text('保存')").click(); pg.wait_for_timeout(900)
    pg.screenshot(path=f"{SHOT}/10_finance_execution.png", full_page=True)
    pg.click(".tabbar >> text=财务信息"); pg.wait_for_selector("text=登记")
    pg.locator(".inline-form input").nth(0).fill("50000"); pg.click(".inline-form >> text=登记"); pg.wait_for_timeout(800)
    c.close()

    c, pg = ctx(); login(pg, "sales")
    pg.goto(BASE + "/execution"); pg.wait_for_timeout(1500)
    body = pg.inner_text("body")
    check("销售可见执行进度（技术资料确认 已完成）", "滨海煤码头" in body and "已完成" in body)
    check("销售执行页无金额", "50,000" not in body and "¥" not in body)
    pg.screenshot(path=f"{SHOT}/11_sales_execution_view.png", full_page=True)
    pid = pg.request.get(f"{BASE}/api/projects/?q=滨海").json()[0]["id"]
    r = pg.request.get(f"{BASE}/api/projects/{pid}/finance/")
    check("销售请求财务 API 被拒", r.status == 403)
    rel = pg.request.get(f"{BASE}/api/projects/{pid}/related/").text()
    check("销售项目关联接口不含金额字段", "contract_amount" not in rel and "internal_amount" not in rel and "total_gross" not in rel)
    c.close()

    # ---- 移动端 ----
    c, pg = ctx(390, 844); login(pg, "sales")
    pg.wait_for_selector(".kpi")
    ow = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    check("移动端无页面级横向溢出", not ow)
    pg.screenshot(path=f"{SHOT}/12_mobile_sales.png")
    c.close()

    # ---- 管理员设置 ----
    c, pg = ctx(); login(pg, "admin")
    pg.goto(BASE + "/settings"); pg.wait_for_selector("text=账号与角色")
    pg.screenshot(path=f"{SHOT}/13_admin_settings.png", full_page=True)
    c.close()
    b.close()

check("浏览器无 JS 运行错误", not [e for e in errors if "403" not in e and "Forbidden" not in e], "; ".join(errors[:5]))
json.dump([{"name": n, "pass": ok, "detail": d} for n, ok, d in results], open(f"{SHOT}/e2e_results.json", "w"), ensure_ascii=False, indent=1)
print(f"\n{sum(1 for _, ok, _ in results if ok)}/{len(results)} PASS")
