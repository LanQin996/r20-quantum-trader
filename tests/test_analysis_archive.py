"""Analysis acceptance tests use temporary archives and fake exchange responses only."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from r20_backend.analysis_store import Archive, lifecycle_id, normalize_position, timestamp_ms, sanitize
from r20_backend.analysis_metrics import summarize, breakdown
from r20_backend.analysis_sync import sync_source, reconcile
from r20_backend import analysis_capture as capture, analysis_service as service
from r20_backend.analysis_routes import install_routes

START = 1788825600000
ACCOUNT = "okx:live:test-account"

def position(i=1, **overrides):
    return {"posId":str(i),"instId":"BTC-USDT-SWAP","direction":"long",
            "cTime":str(START+i*60000),"uTime":str(START+i*60000+30000),
            "type":"2","openAvgPx":"100","closeAvgPx":"102","closeTotalPos":"1","lever":"3",
            "pnl":"2","fee":"-0.1","fundingFee":"-0.2","liqPenalty":"0","settledPnl":"0",
            "realizedPnl":"1.7",**overrides}

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root/"r20_quant.db"
        self.env = patch.dict(os.environ,{"R20_TESTING":"1","R20_ANALYSIS_DB":str(self.path)})
        self.env.start()
        self.archive = Archive(self.path)
        self.saved = patch.object(service,"saved_configuration",return_value={"risk":{"min_rr":2},"prompts":"当前配置"})
        self.saved.start()

    def tearDown(self):
        self.saved.stop(); self.env.stop(); self.temp.cleanup()

    def test_read_only_queries_do_not_create_archive(self):
        self.assertEqual(self.archive.trades(ACCOUNT),[])
        self.assertEqual(self.archive.events(ACCOUNT),[])
        self.assertFalse(self.path.exists())

    def test_same_minute_sides_and_reopened_positions_have_distinct_ids(self):
        p=position()
        opposite=position(direction="short")
        reopened=position(cTime=str(START+90000))
        self.assertEqual(len({lifecycle_id(p),lifecycle_id(opposite),lifecycle_id(reopened)}),3)
        self.archive.upsert_trades(ACCOUNT,[normalize_position(p),normalize_position(opposite),normalize_position(reopened)])
        self.assertEqual(len(self.archive.trades(ACCOUNT)),3)

    def test_costs_precision_breakeven_partial_and_missing_costs(self):
        rows=[normalize_position(position(i,realizedPnl=v)) for i,v in enumerate(["0.000001","-1","0","2"],1)]
        rows += [normalize_position(position(5,type="1",realizedPnl="99"))]
        missing=position(6); missing.pop("realizedPnl"); missing.pop("fundingFee")
        rows.append(normalize_position(missing))
        stat=summarize(rows)
        self.assertEqual((stat["wins"],stat["losses"],stat["breakeven"]),(2,1,1))
        self.assertEqual(stat["sample_count"],4)
        self.assertEqual(stat["closed_count"],5)
        self.assertEqual(stat["incomplete_count"],1)
        self.assertEqual(stat["win_rate"],50)
        self.assertAlmostEqual(stat["net_pnl"],1.000001)
        self.assertAlmostEqual(stat["profit_factor"],2.000001)
        self.assertEqual(stat["max_realized_drawdown"],1)
        self.assertIsNone(summarize([])["win_rate"])
        self.assertIsNone(summarize(rows[:1])["profit_factor"])

    def test_cross_swap_blank_settlement_fields_stay_statistically_visible(self):
        blank=position(7,settledPnl="",nonSettleAvgPx="",realizedPnl="1.7")
        t=normalize_position(blank)
        self.assertTrue(t["cost_complete"])
        self.assertEqual(t["net_pnl"],"1.7")
        self.assertEqual(t["other_settlement"],"0")
        stat=summarize([t])
        self.assertEqual(stat["sample_count"],1)
        self.assertEqual(stat["win_rate"],100)
        self.assertAlmostEqual(stat["net_pnl"],1.7)

    def test_absent_component_is_zero_only_when_realized_identity_proves_it(self):
        proven=position(8); proven.pop("settledPnl")
        self.assertTrue(normalize_position(proven)["cost_complete"])
        unproven=position(9,realizedPnl="9"); unproven.pop("settledPnl")
        self.assertFalse(normalize_position(unproven)["cost_complete"])

    def test_exchange_realized_not_double_deducted(self):
        t=normalize_position(position())
        self.assertEqual(t["net_pnl"],"1.7")
        p=position(); p.pop("realizedPnl")
        self.assertEqual(normalize_position(p)["net_pnl"],"1.7")
        rebate=position(realizedPnl="2.2",fee="0.2",fundingFee="0")
        self.assertEqual(normalize_position(rebate)["net_pnl"],"2.2")

    def test_upsert_is_idempotent_and_separates_account_environments(self):
        t=normalize_position(position())
        for _ in range(2): self.archive.upsert_trades(ACCOUNT,[t])
        self.archive.upsert_trades("okx:demo:test-account",[t])
        self.assertEqual(len(self.archive.trades(ACCOUNT)),1)
        self.assertEqual(len(self.archive.trades("okx:demo:test-account")),1)
        self.archive.upsert_trades(ACCOUNT,[{**t,"status":"partial"}])
        self.assertEqual(self.archive.trades(ACCOUNT)[0]["status"],"closed")

    def test_durable_pagination_resumes_failure_beyond_one_hundred(self):
        rows=[position(i) for i in range(260,0,-1)]
        fail=[True]
        def fetch(source,cursor):
            selected=[r for r in rows if not cursor or int(r["uTime"])<int(cursor)]
            if cursor and fail[0]:
                fail[0]=False
                raise OSError("transient read failure")
            return selected[:100]
        s=sync_source(self.archive,ACCOUNT,"positions-history",fetch)
        self.assertFalse(s["complete"])
        self.assertTrue(s["pending"])
        restarted=Archive(self.path)
        s=sync_source(restarted,ACCOUNT,"positions-history",fetch,max_pages=4)
        self.assertTrue(s["complete"])
        self.assertEqual(len(restarted.raw_rows(ACCOUNT,"positions-history")),260)
        sync_source(restarted,ACCOUNT,"positions-history",fetch,max_pages=4)
        self.assertEqual(len(restarted.raw_rows(ACCOUNT,"positions-history")),260)

    def test_old_partial_revision_cannot_overwrite_new_closed_position(self):
        newest = position(type="2",uTime=str(START+180000))
        older = position(type="1",uTime=str(START+90000),realizedPnl="0.1")
        self.archive.raw(ACCOUNT,"positions-history",[newest,older])
        out = reconcile(self.archive,ACCOUNT,[])
        self.assertEqual(out[0]["status"],"closed")
        self.assertEqual(out[0]["net_pnl"],"1.7")

    def test_new_head_while_backfilling_does_not_lose_history(self):
        rows=[position(i) for i in range(220,0,-1)]
        def fetch(_,cursor): return [r for r in rows if not cursor or int(r["uTime"])<int(cursor)][:100]
        sync_source(self.archive,ACCOUNT,"positions-history",fetch,max_pages=1)
        rows[:0]=[position(i) for i in range(370,220,-1)]
        for _ in range(5):
            sync_source(self.archive,ACCOUNT,"positions-history",fetch,max_pages=3)
        self.assertEqual(len(self.archive.raw_rows(ACCOUNT,"positions-history")),370)
        self.assertTrue(self.archive.sync_state(ACCOUNT,"positions-history")["complete"])

    def test_reconcile_keeps_retired_instruments_and_partial_is_not_closed(self):
        self.archive.raw(ACCOUNT,"positions-history",[position(1,instId="RETIRED-USDT-SWAP"),position(2,type="1")])
        out=reconcile(self.archive,ACCOUNT,[])
        self.assertEqual({t["inst"] for t in out},{"RETIRED","BTC"})
        self.assertEqual(summarize(out)["sample_count"],1)

    def seed_linked_trade(self):
        row=position()
        tid=lifecycle_id(row)
        cfg=self.archive.configuration(ACCOUNT,{"prompt":"旧提示词","risk":{"min_rr":2}},"runtime","brain",123)
        self.archive.event(ACCOUNT,"llm.request",{"request":{"messages":[{"role":"user","content":"历史实际行情"}]}},"observed",
             id="request",occurred_ms=START-1000,cycle_id="brain-cycle",config_id=cfg)
        self.archive.event(ACCOUNT,"decision.proposed",{"proposal":{"action":"BUY_LONG"}},"proposed",
             id="decision",occurred_ms=START,cycle_id="brain-cycle",decision_id="d1",config_id=cfg,inst="BTC-USDT-SWAP")
        self.archive.event(ACCOUNT,"order.submitted",{"effective":{"price":"100"}},"accepted",
             id="order",occurred_ms=START+1000,cycle_id="execute-cycle",decision_id="d1",order_id="o1",config_id=cfg,inst="BTC-USDT-SWAP")
        self.archive.raw(ACCOUNT,"positions-history",[row])
        self.archive.raw(ACCOUNT,"orders",[{"ordId":"o1","instId":"BTC-USDT-SWAP","posSide":"long","state":"filled","uTime":row["cTime"]}])
        self.archive.raw(ACCOUNT,"fills",[{"billId":"f1","ordId":"o1","instId":"BTC-USDT-SWAP","ts":row["cTime"],"fillPx":"100"}])
        reconcile(self.archive,ACCOUNT,[])
        return cfg,tid

    def test_exact_fill_association_and_out_of_window_request_export(self):
        cfg,tid=self.seed_linked_trade()
        t=self.archive.trades(ACCOUNT)[0]
        self.assertEqual(t["decision_id"],"d1")
        self.assertEqual(t["id"],tid)
        query={"start_ms":START+60000,"end_ms":START+120000,"status":"closed"}
        bundle=service.export_bundle(ACCOUNT,query,self.archive)
        with zipfile.ZipFile(io.BytesIO(bundle)) as z:
            manifest=json.loads(z.read("manifest.json"))
            for filename,info in manifest["files"].items():
                self.assertEqual(hashlib.sha256(z.read(filename)).hexdigest(),info["sha256"])
            events=[json.loads(line) for line in z.read("events.jsonl").decode("utf-8").splitlines()]
            self.assertIn("request",{e["id"] for e in events})
            config=json.loads(z.read("configurations.json"))
            self.assertEqual(config[0]["body"]["prompt"],"旧提示词")
            self.assertEqual(json.loads(z.read("summary.json"))["statistics"]["net_pnl"],1.7)
            self.assertEqual(manifest["counts"]["trades"],1)
            self.assertIn("历史实际行情",z.read("events.jsonl").decode("utf-8"))

    def test_nearby_order_without_fill_is_not_misrepresented_as_exact(self):
        self.archive.raw(ACCOUNT,"positions-history",[position()])
        self.archive.raw(ACCOUNT,"orders",[{"ordId":"o1","instId":"BTC-USDT-SWAP","posSide":"long","uTime":str(START+60000)}])
        self.archive.event(ACCOUNT,"order.submitted",{},"accepted",order_id="o1",decision_id="d1")
        result=reconcile(self.archive,ACCOUNT,[])
        self.assertFalse(result[0].get("decision_id"))

    def test_configuration_immutable_current_separate_and_body_deduplicated(self):
        a=self.archive.configuration(ACCOUNT,{"risk":{"rr":2}},"runtime","worker",1)
        same=self.archive.configuration(ACCOUNT,{"risk":{"rr":2}},"runtime","worker",1)
        b=self.archive.configuration(ACCOUNT,{"risk":{"rr":3}},"runtime","worker",1)
        self.assertEqual(a,same); self.assertNotEqual(a,b)
        self.assertEqual(service.configuration_detail(ACCOUNT,a,self.archive)["body"]["risk"]["rr"],2)
        self.assertEqual(service.configuration_detail(ACCOUNT,"current",self.archive)["source"],"saved")
        self.assertIsNone(service.configuration_detail("okx:demo:other",a,self.archive))

    def test_credentials_redacted_in_nested_body_and_free_text(self):
        value={"api_key":"abc-secret-123","max_tokens":4096,"prompt":"Authorization: Bearer abc-secret-123; sk-abcdefghijklm1234"}
        out=sanitize(value,secrets={"abc-secret-123"})
        text=json.dumps(out)
        self.assertNotIn("abc-secret-123",text)
        self.assertNotIn("sk-abcdefghijklm1234",text)
        self.assertEqual(out["max_tokens"],4096)

    def test_old_sqlite_history_is_preserved_without_claiming_current_account(self):
        import sqlite3
        con=sqlite3.connect(self.path)
        con.execute("CREATE TABLE trades (bill_id TEXT,inst TEXT,direction TEXT,action TEXT,time TEXT,price REAL,size REAL,fee REAL,gross_pnl REAL,pnl REAL,comment TEXT)")
        con.execute("INSERT INTO trades VALUES ('old-1','RETIRED','多','closed','2026-09-01 12:00:00',1,2,-0.1,1,0.9,'旧推断原因')")
        con.commit(); con.close()
        with patch.object(capture,"ROOT",self.root):
            capture.recover_legacy()
            capture.recover_legacy()
        rows=self.archive.trades("legacy:unknown")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["inst"],"RETIRED")
        self.assertIsNone(rows[0]["net_pnl"])
        self.assertEqual(rows[0]["known_net_pnl"],0.9)
        self.assertFalse(self.archive.trades(ACCOUNT))

    def test_capture_failure_does_not_prevent_execution(self):
        @capture.observed("test.protection")
        def protected(x): return x+1
        with patch.object(Archive,"event",side_effect=OSError("disk full")),patch.object(capture,"fault") as fault:
            self.assertEqual(protected(2),3)
            self.assertTrue(fault.called)

    def test_authenticated_read_api_and_full_export_not_page_only(self):
        app=FastAPI()
        def auth(x_r20_session=None):
            if x_r20_session!="test": raise HTTPException(status_code=401)
        install_routes(app,auth)
        client=TestClient(app)
        self.archive.upsert_trades(ACCOUNT,[normalize_position(position(i)) for i in range(1,45)])
        query=f"?account={ACCOUNT}&start=2026-09-01&end=2026-10-01"
        headers={"X-R20-Session":"test"}
        for endpoint in ("summary","trades","events","configurations/current","export"):
            self.assertEqual(client.get("/api/v1/admin/analysis/"+endpoint+query).status_code,401)
        response=client.get("/api/v1/admin/analysis/trades"+query,headers=headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()["items"]),20)
        self.assertEqual(response.json()["total"],44)
        summary=client.get("/api/v1/admin/analysis/summary"+query,headers=headers).json()
        export=client.get("/api/v1/admin/analysis/export"+query,headers=headers)
        with zipfile.ZipFile(io.BytesIO(export.content)) as z:
            self.assertEqual(len(json.loads(z.read("trades.json"))),44)
            self.assertEqual(summary["statistics"],json.loads(z.read("summary.json"))["statistics"])
        self.assertEqual(client.get("/api/v1/admin/analysis/trades"+query+"&page=0",headers=headers).status_code,422)
        self.assertEqual(client.get("/api/v1/admin/analysis/summary?start=invalid",headers=headers).status_code,422)

if __name__=="__main__":
    unittest.main()
