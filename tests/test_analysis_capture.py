"""Replay actual capture hooks without exchange or model network calls."""
from __future__ import annotations
import concurrent.futures
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
from r20_backend import analysis_capture as capture
from r20_backend.analysis_store import Archive

class CaptureReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"analysis.db"
        self.env=patch.dict(os.environ,{"R20_TESTING":"1","R20_ANALYSIS_DB":str(self.path)})
        self.env.start()
        self.archive=Archive(self.path)
        self.account="okx:live:replay"
        self.scope=capture.scope(account=self.account,cycle_id="cycle-replay",config_id="test-config")
        self.scope.__enter__()

    def tearDown(self):
        self.scope.__exit__(None,None,None); self.env.stop(); self.temp.cleanup()

    def events(self,kind=None):
        with self.archive.connect() as con:
            return [{**e,"body":self.archive.read_blob(con,e["body_hash"])} for e in self.archive.events(self.account,con) if kind is None or e["kind"]==kind] if con else []

    def test_actual_fallback_request_and_response_are_recorded(self):
        from r20_backend import llm_manager
        response={"choices":[{"message":{"content":'{"action":"WAIT"}'}}],"usage":{"total_tokens":25}}
        class Reply(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        calls=[]
        def transport(req,timeout):
            payload=json.loads(req.data.decode("utf-8")); calls.append(payload)
            if len(calls)==1:
                raise urllib.error.HTTPError(req.full_url,400,"invalid parameter",{},io.BytesIO(b"reasoning_effort invalid parameter"))
            return Reply(json.dumps(response).encode("utf-8"))
        runtime={"model":"replay-model","base_url":"https://example.test/v1","api_key":"secret-for-test","api_format":"openai_chat","reasoning_type":"none","thinking_timeout":30}
        with patch.object(llm_manager,"get_active_llm_runtime",return_value=runtime),patch.object(llm_manager.urllib.request,"urlopen",side_effect=transport):
            content,_,usage,_=llm_manager.execute_llm_request([{"role":"user","content":"实际行情和持仓"}],reasoning_effort="none")
        requests=self.events("llm.request")
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0]["body"]["request"],calls[0])
        self.assertEqual(requests[1]["body"]["request"],calls[1])
        self.assertNotIn("temperature",requests[1]["body"]["request"])
        recorded=self.events("llm.response")[0]
        self.assertEqual(recorded["body"]["response"],response)
        self.assertEqual(recorded["body"]["request_id"],requests[-1]["body"]["request_id"])
        self.assertEqual(usage["total_tokens"],25)

    def test_parallel_seats_keep_cycle_but_separate_spans(self):
        @capture.observed("council.test")
        def seat(name):
            capture.emit("llm.response",{"seat":name})
            return name
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[capture.threaded_submit(pool,seat,n) for n in ("a","b")]
            self.assertEqual({f.result() for f in futures},{"a","b"})
        events=self.events("llm.response")
        self.assertEqual({e["cycle_id"] for e in events},{"cycle-replay"})
        self.assertEqual(len({e["body"]["capture_span_id"] for e in events}),2)

    def test_rejected_plugin_records_later_rule_as_not_executed(self):
        from r20_backend import interceptor_manager as manager
        plugins=[{"filename":"first.py","enabled":True},{"filename":"second.py","enabled":True}]
        for p in plugins: (Path(self.temp.name)/p["filename"]).write_text("# fixture\n",encoding="utf-8")
        checker=SimpleNamespace(check_risk=lambda p,d,c:(False,"blocked by fixture"))
        with patch.object(manager,"PLUGINS_DIR",Path(self.temp.name)),patch.object(manager,"list_plugins",return_value=plugins),patch.object(manager,"_load_module_from_file",return_value=checker) as loader:
            action,reason,rr=manager.run_interceptor_pipeline(
                {"instId":"BTC-USDT-SWAP","data_quality":"valid"},
                {"action":"BUY_LONG","entry_price":100,"take_profit_price":130,"stop_loss_price":90,"confidence":90},{})
        self.assertEqual(action,"WAIT")
        self.assertEqual(loader.call_count,1)
        rules={e["body"]["rule"]:e for e in self.events("risk.rule")}
        self.assertEqual(rules["first.py"]["status"],"rejected")
        self.assertEqual(rules["second.py"]["status"],"not_executed")
        self.assertEqual(rules["quote_geometry_rr"]["body"]["inputs"]["rr"],3)

    def test_order_submission_preserves_exchange_acceptance_not_fill(self):
        scripts=str(Path(__file__).resolve().parents[1]/"scripts")
        if scripts not in sys.path: sys.path.insert(0,scripts)
        trader=importlib.import_module("scripts.ai_factor_trader")
        with capture.scope(inst="BTC-USDT-SWAP",decision_id="decision-replay"):
            with patch.object(trader,"selected_environment",return_value=SimpleNamespace(simulated=False)),patch.object(trader,"okx_private_command",side_effect=lambda c:c),patch.object(trader,"run_cmd_result",return_value={"ok":True,"data":[{"ordId":"order-replay"}]}):
                result=trader.submit_protected_limit_order("BTC-USDT-SWAP","buy","long",2,100,130,90)
        self.assertEqual(result,(True,"order-replay"))
        event=self.events("order.submitted")[0]
        self.assertEqual(event["order_id"],"order-replay")
        self.assertEqual(event["decision_id"],"decision-replay")
        self.assertEqual(event["body"]["effective"]["sl"],90)
        self.assertFalse(self.events("order.fill"))

    def test_order_rejection_does_not_submit(self):
        scripts=str(Path(__file__).resolve().parents[1]/"scripts")
        if scripts not in sys.path: sys.path.insert(0,scripts)
        trader=importlib.import_module("scripts.ai_factor_trader")
        with patch.object(trader,"selected_environment",return_value=SimpleNamespace(simulated=False)),patch.object(trader,"run_cmd_result") as submit:
            accepted,_=trader.submit_protected_limit_order("BTC-USDT-SWAP","buy","long",2,100,110,90)
        self.assertFalse(accepted); submit.assert_not_called()
        gate=self.events("execution.gate")[0]
        self.assertEqual(gate["status"],"rejected")

    def test_parallel_config_reads_do_not_rewrite_unchanged_configuration(self):
        from r20_backend import llm_manager, config
        path=Path(self.temp.name)/"models.json"
        fake=SimpleNamespace(llm_base_url="",llm_api_key="",llm_model="",llm_reasoning_effort="high")
        with patch.object(llm_manager,"LLM_CONFIG_FILE",path),patch.object(config,"settings",fake):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                rows=list(pool.map(lambda _:llm_manager.init_llm_config(),range(12)))
            stamp=path.stat().st_mtime_ns
            with patch.object(llm_manager,"_atomic_write_json",wraps=llm_manager._atomic_write_json) as writer:
                llm_manager.init_llm_config()
            writer.assert_not_called()
            self.assertEqual(path.stat().st_mtime_ns,stamp)
            self.assertEqual(len(rows),12)

    def test_saved_risk_change_does_not_change_loaded_snapshot(self):
        with patch.object(capture,"saved_configuration",side_effect=[{"risk":{"R20_MAX_LEVERAGE":{"configured":"3"}}},{"risk":{"R20_MAX_LEVERAGE":{"configured":"5"}}}]):
            a=capture.configuration("worker",{"MAX_LEVERAGE":3})
            b=capture.configuration("worker",{"MAX_LEVERAGE":3})
        self.assertNotEqual(a,b)
        with self.archive.connect() as con:
            rows=self.archive.configurations(self.account,con)
            bodies=[self.archive.read_blob(con,r["body_hash"]) for r in rows]
        self.assertEqual({b["loaded_risk"]["R20_MAX_LEVERAGE"] for b in bodies},{3})

if __name__=="__main__":
    unittest.main()
