"""Authenticated analysis queries and deterministic, self-contained exports."""
from __future__ import annotations
import csv
from datetime import datetime, timedelta
import hashlib
import io
import json
import zipfile
from r20_backend.analysis_store import Archive, BJ, now_ms, timestamp_ms, sanitize, time_text, digest
from r20_backend.analysis_metrics import summarize, breakdown, VERSION, decimal
from r20_backend.analysis_capture import saved_configuration, fault_status, identity

def filters(start: str | None = None, end: str | None = None, inst: str = "", side: str = "", config_id: str = "", result: str = "", status: str = "closed") -> dict:
    today = datetime.now(BJ).date()
    # Calendar dates use Beijing midnight; end is exclusive.
    start_ms = timestamp_ms(start or str(today-timedelta(days=29)))
    end_ms = timestamp_ms(end or str(today+timedelta(days=1)))
    if start_ms is None or end_ms is None or not 0 <= start_ms < end_ms <= 253402300799999: raise ValueError("时间范围无效；结束时间为不包含的边界")
    if side not in ("","all","long","short","unknown"): raise ValueError("方向无效")
    if result not in ("","all","win","loss","breakeven"): raise ValueError("盈亏筛选无效")
    if status not in ("all","closed","holding","partial","unknown"): raise ValueError("状态筛选无效")
    return {"start_ms":start_ms,"end_ms":end_ms,"inst":inst,"side":"" if side=="all" else side,"config_id":config_id,"result":"" if result=="all" else result,"status":status}

def selected_events(events: list[dict], trades: list[dict], query: dict) -> list[dict]:
    ids = {t["id"] for t in trades}
    decisions = {v for t in trades for v in ([t.get("decision_id")]+t.get("decision_ids",[])) if v}
    orders = {v for t in trades for v in t.get("order_ids",[])}
    cycles = {t.get("cycle_id") for t in trades if t.get("cycle_id")}
    def in_window(e):
        if not query.get("start_ms",0)<=e["occurred_ms"]<query.get("end_ms",now_ms()+1): return False
        if query.get("inst") and e["inst"].replace("-USDT-SWAP","") not in ("",query["inst"]): return False
        if query.get("side") not in ("",None,"all") and e["side"] not in ("",query["side"]): return False
        if query.get("config_id") and e["config_id"]!=query["config_id"]: return False
        return True
    selected = set()
    # Include complete evidence for selected trades plus observations in the requested window.
    for _ in range(4):
        previous = len(selected)
        for e in events:
            if in_window(e) or e["trade_id"] in ids or e["decision_id"] in decisions or e["order_id"] in orders or e["cycle_id"] in cycles:
                selected.add(e["id"])
                if e["decision_id"]: decisions.add(e["decision_id"])
                if e["order_id"]: orders.add(e["order_id"])
                if e["cycle_id"]: cycles.add(e["cycle_id"])
        if len(selected)==previous: break
    return [e for e in events if e["id"] in selected]

def window_events(events: list[dict], query: dict) -> list[dict]:
    """Metrics count events in the requested window; export may carry older context."""
    return [e for e in events
        if query.get("start_ms",0) <= e["occurred_ms"] < query.get("end_ms",now_ms()+1)
        and (not query.get("inst") or e["inst"].replace("-USDT-SWAP","")==query["inst"])
        and (query.get("side") in ("",None,"all") or e["side"]==query["side"])
        and (not query.get("config_id") or e["config_id"]==query["config_id"])]

def execution_summary(events: list[dict], read_body) -> dict:
    from collections import Counter
    proposed = {e["decision_id"] for e in events if e["kind"]=="decision.proposed" and e["status"]=="proposed"}
    passed = {e["decision_id"] for e in events if e["kind"]=="decision.filtered" and e["status"]=="passed"}
    orders = {e["order_id"] for e in events if e["kind"]=="order.submitted" and e["order_id"]}
    fills = {e["order_id"] for e in events if e["kind"]=="order.fill" and e["order_id"]}
    rejections = Counter()
    for e in events:
        if e["status"] in ("rejected","failed") and e["kind"] in ("risk.rule","decision.filtered","execution.gate"):
            b = read_body(e["body_hash"]) or {}
            rejections[str(b.get("reason") or b.get("rule") or e["kind"])] += 1
    return {"proposed_decisions":len(proposed-{""}),"passed_decisions":len(passed-{""}),
            "submitted_orders":len(orders),"filled_orders":len(fills),
            "rejections":[{"reason":r,"count":n} for r,n in rejections.most_common()],
            "note":"执行漏斗按事件发生时间统计：建议／通过按决策计数，提交／成交按订单计数；被拒绝订单不估算盈亏"}

def load_summary(account: str, query: dict, archive: Archive | None = None) -> dict:
    archive = archive or Archive()
    with archive.connect() as con:
        trades = archive.trades(account,query,con) if con else []
        all_trades = archive.trades(account,con=con) if con else []
        events = selected_events(archive.events(account,con),trades,query) if con else []
        configs = archive.configurations(account,con) if con else []
        last = {}
        for e in events:
            if e["config_id"]: last[e["config_id"]] = max(last.get(e["config_id"],0),e["observed_ms"])
        for c in configs:
            c.pop("body_hash",None)
            c["last_observed_ms"] = last.get(c["id"],c["captured_ms"])
        health = archive.health(account,con) if con else archive.health(account)
        health["capture_fault"] = fault_status()
        return {"account":account,"accounts":archive.accounts(),"filters":query,"timezone":"Asia/Shanghai",
                "statistics":summarize(trades),"instruments":sorted({t["inst"] for t in all_trades}),
                "configurations":configs,"health":health,
                "execution":execution_summary(window_events(events,query),lambda key:archive.read_blob(con,key)) if con else {},
                "captured_at_ms":now_ms()}

def trade_list(account: str, query: dict, page: int = 1, page_size: int = 20, archive=None):
    archive = archive or Archive()
    if query.get("result"):
        all_rows = list(reversed(archive.trades(account, query)))
        items, total = all_rows[(page-1)*page_size:page*page_size], len(all_rows)
    else:
        with archive.connect() as con:
            items, total = archive.trade_page(account, query, page, page_size, con) if con else ([], 0)
        items = list(reversed(items))
    return {"items":items,"total":total,"page":page,"page_size":page_size,"statistics_version":VERSION}

def trade_detail(account: str, trade_id: str, archive=None) -> dict | None:
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return None
        r = con.execute("SELECT body_hash FROM analysis_trades WHERE account=? AND id=?",(account,trade_id)).fetchone()
        if not r: return None
        trade = archive.read_blob(con,r[0])
        query = {"start_ms":1,"end_ms":1}  # Only explicit relation expansion, no time heuristic.
        events = selected_events(archive.events(account,con),[trade],query)
        configs = sorted({e["config_id"] for e in events if e["config_id"]}|{trade["config_id"]} if trade.get("config_id") else {e["config_id"] for e in events if e["config_id"]})
        return {"trade":trade,"events":[{k:v for k,v in e.items() if k!="body_hash"} for e in events],"config_ids":configs}

def event_list(account: str, query: dict, page: int = 1, page_size: int = 30, archive=None):
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return {"items":[],"total":0}
        trades = archive.trades(account,query,con)
        events = list(reversed(selected_events(archive.events(account,con),trades,query)))
        return {"items":[{k:v for k,v in e.items() if k!="body_hash"} for e in events[(page-1)*page_size:page*page_size]],"total":len(events)}

def event_detail(account: str, event_id: str, archive=None):
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return None
        r = con.execute("SELECT * FROM analysis_events WHERE account=? AND id=?",(account,event_id)).fetchone()
        if not r: return None
        e = dict(r); e["body"] = archive.read_blob(con,e.pop("body_hash"))
        return sanitize(e)

def configuration_detail(account: str, config_id: str, archive=None):
    if config_id=="current":
        body = saved_configuration()
        return {"id":digest(body),"source":"saved","captured_ms":now_ms(),"body":body,
                "note":"导出时保存的配置，不代表交易进程已加载"}
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return None
        r = con.execute("SELECT * FROM analysis_configs WHERE account=? AND id=?",(account,config_id)).fetchone()
        if not r: return None
        out = dict(r); out["body"]=archive.read_blob(con,out.pop("body_hash"))
        return sanitize(out)

def export_bundle(account: str, query: dict, archive=None) -> bytes:
    archive = archive or Archive()
    cutoff = now_ms()
    with archive.connect() as con:
        if con:
            con.execute("SELECT COUNT(*) FROM analysis_events").fetchone()
        cutoff = now_ms()
        trades = archive.trades(account,query,con) if con else []
        events = selected_events(archive.events(account,con),trades,query) if con else []
        configs = archive.configurations(account,con) if con else []
        used = {e["config_id"] for e in events if e["config_id"]}|{t["config_id"] for t in trades if t.get("config_id")}
        # Include most recently observed runtime snapshots, separately from trade-time configs.
        runtime = {}
        for e in archive.events(account,con) if con else []:
            if e["kind"]=="cycle.start" and e["config_id"]:
                cfg = next((c for c in configs if c["id"]==e["config_id"]),None)
                if cfg: runtime[cfg["process"]]={"config_id":cfg["id"],"last_observed_ms":e["observed_ms"],"pid":cfg["pid"]}
        latest = {}
        for e in archive.events(account,con) if con else []:
            if e["kind"] in ("account.snapshot", "execution.cycle_state"):
                latest[e["kind"]] = {**{k:v for k,v in e.items() if k!="body_hash"},
                    "body":archive.read_blob(con,e["body_hash"])}
        used |= {e["config_id"] for e in latest.values() if e["config_id"]}
        used |= {v["config_id"] for v in runtime.values()}
        config_bodies = [{**{k:v for k,v in c.items() if k!="body_hash"},"body":archive.read_blob(con,c["body_hash"])} for c in configs if c["id"] in used]
        event_bodies = [{**{k:v for k,v in e.items() if k!="body_hash"},"body":archive.read_blob(con,e["body_hash"])} for e in events]
        # Exchange facts are needed to independently reconcile cost and fill allocations.
        order_ids = {v for t in trades for v in t.get("order_ids",[])}|{e["order_id"] for e in events if e["order_id"]}
        position_ids = {t["id"] for t in trades}
        from r20_backend.analysis_store import lifecycle_id
        raw = {}
        for source in ("positions-history","orders","fills","bills"):
            records = archive.raw_rows(account,source,con) if con else []
            raw[source] = [r for r in records if
                (source=="positions-history" and lifecycle_id(r) in position_ids) or
                (str(r.get("ordId") or "") in order_ids) or
                any(t.get("inst_id")==r.get("instId") and (t.get("open_ms") or 0)
                    <= (timestamp_ms(r.get("ts") or r.get("uTime")) or 0)
                    <= (t.get("close_ms") or cutoff) for t in trades) or
                (query["start_ms"] <= (timestamp_ms(r.get("ts") or r.get("uTime")) or 0) < query["end_ms"]
                 and (not query.get("inst") or str(r.get("instId","")).replace("-USDT-SWAP","")==query["inst"]))]
        health = archive.health(account,con) if con else archive.health(account)
        stats = summarize(trades)
        execution = execution_summary(window_events(events,query), lambda key: archive.read_blob(con, key)) if con else {}
    current = configuration_detail(account,"current",archive)
    summary = {"statistics":stats,"filters":query,"health":health,"runtime_observations":runtime,"account":account,
               "execution":execution}
    files = {}
    def add_json(name,body):
        files[name] = json.dumps(sanitize(body),ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8")
    add_json("summary.json",summary)
    add_json("trades.json",trades)
    add_json("configurations.json",config_bodies)
    add_json("current_configuration.json",current)
    add_json("latest_runtime_state.json", {"observations":latest,"note":"账户最近一次归档观测，非导出时实时交易所查询"})
    add_json("exchange_facts.json",raw)
    files["events.jsonl"] = ("\n".join(json.dumps(sanitize(e),ensure_ascii=False,allow_nan=False) for e in event_bodies)+("\n" if events else "")).encode("utf-8")
    columns = ["id","inst","side","status","open_time","close_time","open_px","close_px","sz","lever",
               "gross_pnl","fee","funding_fee","other_settlement","net_pnl","known_net_pnl","cost_complete",
               "cost_basis","exit_reason","exit_evidence","decision_id","config_id","confidence","entry_deviation_bps"]
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf,fieldnames=columns,extrasaction="ignore",lineterminator="\n")
    writer.writeheader()
    for row in sanitize(trades):
        csv_row = {k:row.get(k) for k in columns}
        for key,value in csv_row.items():
            if isinstance(value,str) and value.startswith(("=","+","-","@")) and decimal(value) is None:
                csv_row[key]="'"+value
        writer.writerow(csv_row)
    files["trades.csv"] = ("\ufeff"+buf.getvalue()).encode("utf-8")
    rate = f'{stats["win_rate"]:.2f}%' if stats["win_rate"] is not None else "不可计算"
    files["README.md"] = (
        "# R20 交易复盘分析包\n\n"
        f"账户匿名标识：{account}。时区：Asia/Shanghai。归档截止：{time_text(cutoff)}。\n\n"
        f"已平仓 {stats['closed_count']} 笔；成本完整 {stats['sample_count']} 笔；成本不完整 {stats['incomplete_count']} 笔。净胜率：{rate}。\n\n"
        "summary.json 提供口径与覆盖状态；trades.csv/JSON 是全部筛选交易；events.jsonl 按 ID 关联请求、裁决和执行，包含区间外必要证据。\n\n"
        "configurations.json 是事件发生时采集的历史配置；current_configuration.json 是导出时保存值，不能用于替代历史值。"
        "runtime_observations 只表示最近观测，不证明进程现在仍运行。\n\n"
        "净胜率=净盈利笔数/成本完整的已平仓笔数，保本计入分母。利润因子=盈利总额/亏损总额绝对值；"
        "实际盈亏比=平均盈利/平均亏损绝对值。无亏损或无样本时相应比率为 null。\n\n"
        "回撤是已实现收益曲线的绝对回撤，不是账户净值回撤。置信度为模型评分。极值来自巡检采样。"
        "未知费用不按零处理，推断退出原因不当成已确认事实。分组差异不直接证明因果，未成交订单不计算假设盈亏。\n\n"
        "历史覆盖受交易所保留期与采集起点限制。所有文本为 UTF-8，凭证已脱敏。"
        "分析时将事实、假设和待验证建议分开，并引用交易及事件 ID。\n"
    ).encode("utf-8")
    manifest = {"format":"r20-analysis-bundle","version":1,"statistics_version":VERSION,"account":account,
                "timezone":"Asia/Shanghai","filters":query,"archive_cutoff_ms":cutoff,
                "current_configuration_captured_ms":current["captured_ms"],
                "counts":{"trades":len(trades),"events":len(events),"configurations":len(config_bodies)},
                "files":{name:{"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)} for name,data in files.items()}}
    add_json("manifest.json",manifest)
    out = io.BytesIO()
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED) as bundle:
        for name,data in files.items(): bundle.writestr(name,data)
    return out.getvalue()
