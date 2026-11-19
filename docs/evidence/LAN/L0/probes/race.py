"""并发反例：render 与 PATCH 修订竞争（PostgreSQL，barrier 同步起跑）。"""
import threading, json
from django.db import connection
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.quotations.models import QuoteRevision, QuoteLine
res={"unfrozen":0,"lines_after_frozen":0,"trials":0,"errors":[]}
u=User.objects.get(username="assistant")
def cli():
    c=APIClient(); c.force_authenticate(u); return c
for t in range(12):
    c=cli()
    q=c.post("/api/quotations/",{"project":1,"recipient":1,"legal_entity":1},format="json").json()
    rid=q["latest"]["id"]
    c.post(f"/api/quote-revisions/{rid}/lines/",{"product":1,"qty":"1"},format="json")
    bar=threading.Barrier(3)
    def render():
        try: bar.wait(); cli().post(f"/api/quote-revisions/{rid}/render/")
        except Exception as e: res["errors"].append(repr(e)[:100])
        finally: connection.close()
    def patch():
        try: bar.wait(); cli().patch(f"/api/quote-revisions/{rid}/",{"remark":"race"},format="json")
        except Exception as e: res["errors"].append(repr(e)[:100])
        finally: connection.close()
    def addline():
        try: bar.wait(); cli().post(f"/api/quote-revisions/{rid}/lines/",{"product":2,"qty":"1"},format="json")
        except Exception as e: res["errors"].append(repr(e)[:100])
        finally: connection.close()
    ts=[threading.Thread(target=f) for f in (render,patch,addline)]
    [x.start() for x in ts]; [x.join() for x in ts]
    rev=QuoteRevision.objects.get(pk=rid); res["trials"]+=1
    if rev.pdf_sha256 and rev.status=="DRAFT": res["unfrozen"]+=1
    if rev.status=="GENERATED" and rev.snapshot and len(rev.snapshot["lines"])!=QuoteLine.objects.filter(revision=rev).count(): res["lines_after_frozen"]+=1
print(json.dumps(res,ensure_ascii=False))
