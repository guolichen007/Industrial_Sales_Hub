"""L0 反例探针：在 4bab6f2 代码上复现审查发现（预期“漏洞存在”）。"""
import json, threading
from decimal import Decimal
from django.db import connection
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.projects.models import Project
from apps.quotations.models import QuoteRevision
def cli(u):
    c=APIClient(); c.force_authenticate(User.objects.get(username=u)); return c
out={}
fin=cli("finance"); sales=cli("sales"); asst=cli("assistant"); lead=cli("leader")
p=Project.objects.get(code="PRJ-26001")
r=fin.post("/api/order-confirmations/",{"project":p.id,"legal_entity":1,"basis":"CONTRACT","order_ref":"L0-1","contract_amount":"123456.78"},format="json")
out["F1 财务可写合同金额"]=(r.status_code, r.json().get("contract_amount"))
r=fin.get("/api/order-confirmations/"); out["F2 财务可读合同金额"]="contract_amount" in r.content.decode()
r=fin.post(f"/api/projects/{p.id}/finance/",{"kind":"RECEIPT","amount":"1","happened_on":"2026-11-01"},format="json"); out["F3 财务台账可写"]=r.status_code
r=sales.get("/api/projects/"); out["S1 销售可见 quote_count"]="quote_count" in r.content.decode()
r=sales.get(f"/api/projects/{p.id}/"); out["S2 销售可见 expected_amount_note 字段"]="expected_amount_note" in r.content.decode()
r=cli("admin").get("/api/quotations/"); out["A1 管理员可读报价"]=r.status_code
r=cli("admin").patch(f"/api/users/{User.objects.get(username='admin').id}/",{"role":"SALES"},format="json"); out["A2 最后管理员可自降级"]=r.status_code
User.objects.filter(username="admin").update(role="ADMIN")
r=sales.get("/api/quotations/?project=abc"); out["E1 非数字过滤(销售403先拦截)"]=r.status_code
try:
    r=asst.get("/api/quotations/?project=abc"); out["E2 非数字过滤"]=r.status_code
except Exception as e: out["E2 非数字过滤"]="500 未处理异常: "+type(e).__name__
try:
    c=APIClient(); r=c.post("/api/auth/login/",[],format="json"); out["E3 登录数组体"]=r.status_code
except Exception as e: out["E3 登录数组体"]="500 未处理异常: "+type(e).__name__
print(json.dumps(out,ensure_ascii=False,indent=1))
