"""Durable, account-isolated analysis archive. Reads never create a database."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import zlib
from typing import Any
from r20_backend.analysis_metrics import decimal, number

ROOT = Path(__file__).resolve().parents[1]
BJ = timezone(timedelta(hours=8))
SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_blobs (
 hash TEXT PRIMARY KEY, content BLOB NOT NULL, raw_bytes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_configs (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, captured_ms INTEGER NOT NULL,
 source TEXT NOT NULL, process TEXT NOT NULL, pid INTEGER, body_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analysis_configs_account ON analysis_configs(account, captured_ms);
CREATE TABLE IF NOT EXISTS analysis_events (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, occurred_ms INTEGER NOT NULL, observed_ms INTEGER NOT NULL,
 kind TEXT NOT NULL, status TEXT NOT NULL, cycle_id TEXT NOT NULL DEFAULT '',
 decision_id TEXT NOT NULL DEFAULT '', order_id TEXT NOT NULL DEFAULT '',
 trade_id TEXT NOT NULL DEFAULT '', inst TEXT NOT NULL DEFAULT '', side TEXT NOT NULL DEFAULT '',
 config_id TEXT NOT NULL DEFAULT '', body_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analysis_events_time ON analysis_events(account, occurred_ms);
CREATE INDEX IF NOT EXISTS analysis_events_decision ON analysis_events(account, decision_id);
CREATE INDEX IF NOT EXISTS analysis_events_order ON analysis_events(account, order_id);
CREATE INDEX IF NOT EXISTS analysis_events_trade ON analysis_events(account, trade_id);
CREATE INDEX IF NOT EXISTS analysis_events_cycle ON analysis_events(account, cycle_id);
CREATE TABLE IF NOT EXISTS analysis_trades (
 account TEXT NOT NULL, id TEXT NOT NULL, inst TEXT NOT NULL, side TEXT NOT NULL,
 status TEXT NOT NULL, open_ms INTEGER, close_ms INTEGER, config_id TEXT NOT NULL DEFAULT '',
 decision_id TEXT NOT NULL DEFAULT '', body_hash TEXT NOT NULL, updated_ms INTEGER NOT NULL,
 PRIMARY KEY(account,id)
);
CREATE INDEX IF NOT EXISTS analysis_trades_time ON analysis_trades(account, status, close_ms);
CREATE TABLE IF NOT EXISTS analysis_raw (
 account TEXT NOT NULL, source TEXT NOT NULL, id TEXT NOT NULL, occurred_ms INTEGER NOT NULL,
 body_hash TEXT NOT NULL, PRIMARY KEY(account,source,id)
);
CREATE TABLE IF NOT EXISTS analysis_sync (
 account TEXT NOT NULL, source TEXT NOT NULL, body_json TEXT NOT NULL,
 PRIMARY KEY(account,source)
);
PRAGMA user_version=1;
"""
SECRET_KEY = re.compile(r"(^|[_-])(api[_-]?keys?|secret|passphrase|password|authorization|cookies?|credentials?|access[_-]?token|refresh[_-]?token|session([_-]?token)?|close[_-]?token|token[_-]?hash|salt)([_-]|$)", re.I)
REDACTED = "[REDACTED]"

def db_path() -> Path:
    return Path(os.environ.get("R20_ANALYSIS_DB") or ROOT / "data" / "r20_quant.db")

def now_ms() -> int:
    return time.time_ns() // 1_000_000

def timestamp_ms(value: Any) -> int | None:
    if value is None or value == "": return None
    try:
        n = float(value)
        if n != n or abs(n) == float("inf"): return None
        return int(n if abs(n) > 100_000_000_000 else n * 1000)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=BJ)
            return int(dt.timestamp()*1000)
        except ValueError:
            return None

def time_text(ms: Any) -> str:
    return datetime.fromtimestamp(int(ms)/1000, BJ).strftime("%Y-%m-%d %H:%M:%S") if ms is not None else ""

def sensitive_values() -> set[str]:
    values = {v for k,v in os.environ.items() if SECRET_KEY.search(k) and len(v) >= 6}
    # Configs may contain plaintext provider credentials. Never return these values to callers.
    p = ROOT / "data" / "llm_models.json"
    if p.exists():
        try:
            def visit(v):
                if isinstance(v, dict):
                    for k,x in v.items():
                        if SECRET_KEY.search(k) and isinstance(x,str) and len(x)>=6: values.add(x)
                        else: visit(x)
                elif isinstance(v,list):
                    for x in v: visit(x)
            visit(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return values

def sanitize(value: Any, secrets: set[str] | None = None) -> Any:
    secrets = sensitive_values() if secrets is None else secrets
    if isinstance(value, dict):
        return {str(k): REDACTED if SECRET_KEY.search(str(k)) else sanitize(v,secrets) for k,v in value.items()}
    if isinstance(value, (list,tuple,set)):
        return [sanitize(v,secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted(secrets,key=len,reverse=True): value = value.replace(secret,REDACTED)
        value = re.sub(r"(?i)(Bearer\s+)[^\s\"'<>]+",r"\1[REDACTED]",value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}",REDACTED,value)
        value = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[REDACTED]@", value)
        value = re.sub(r"(?i)((?:api[_-]?key|secret[_-]?key|passphrase|authorization|password|access_token|refresh_token|session_token)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'\n}]+",r"\1[REDACTED]",value)
        return value
    if isinstance(value,float) and (value != value or abs(value)==float("inf")): return None
    if isinstance(value,(int,float,bool)) or value is None: return value
    return str(value)

def canonical(value: Any) -> bytes:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

class Archive:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else db_path()

    @contextmanager
    def connect(self, write: bool = False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(self.path),timeout=5.0)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=5000")
            con.executescript(SCHEMA)
        else:
            con = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",uri=True,timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_events'").fetchone():
                con.close()
                yield None
                return
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN")
            yield con
            if write: con.commit()
        except BaseException:
            if write: con.rollback()
            raise
        finally:
            con.close()

    def blob(self, con, body: Any) -> str:
        raw = canonical(sanitize(body))
        key = hashlib.sha256(raw).hexdigest()
        con.execute("INSERT OR IGNORE INTO analysis_blobs VALUES (?,?,?)",(key,zlib.compress(raw),len(raw)))
        return key

    @staticmethod
    def read_blob(con, key: str) -> Any:
        row = con.execute("SELECT content FROM analysis_blobs WHERE hash=?",(key,)).fetchone()
        return json.loads(zlib.decompress(row[0]).decode("utf-8")) if row else None

    def configuration(self, account: str, body: dict, source: str, process: str = "", pid: int | None = None) -> str:
        clean = sanitize(body)
        key = digest({"account":account,"source":source,"process":process,"body":clean})
        with self.connect(True) as con:
            con.execute("INSERT OR IGNORE INTO analysis_configs VALUES (?,?,?,?,?,?,?)",
                (key,account,now_ms(),source,process,pid,self.blob(con,clean)))
        return key

    def event(self, account: str, kind: str, body: Any, status: str = "observed", **meta) -> str:
        event_id = str(meta.get("id") or uuid.uuid4().hex)
        observed = now_ms()
        with self.connect(True) as con:
            con.execute("INSERT OR IGNORE INTO analysis_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event_id,account,int(meta.get("occurred_ms") or observed),observed,kind,status,
                 *[str(meta.get(k) or "") for k in ("cycle_id","decision_id","order_id","trade_id","inst","side","config_id")],
                 self.blob(con,body)))
        return event_id

    def raw(self, account: str, source: str, rows: list[dict]) -> None:
        with self.connect(True) as con:
            for r in rows:
                raw_id = str(r.get("billId") or r.get("tradeId") or r.get("ordId") or digest(r))
                if source == "positions-history": raw_id = lifecycle_id(r)
                stamp = timestamp_ms(r.get("uTime") or r.get("ts") or r.get("cTime")) or 0
                con.execute("""INSERT INTO analysis_raw VALUES (?,?,?,?,?)
                    ON CONFLICT(account,source,id) DO UPDATE SET occurred_ms=excluded.occurred_ms,body_hash=excluded.body_hash WHERE excluded.occurred_ms >= analysis_raw.occurred_ms""",
                    (account,source,raw_id,stamp,self.blob(con,r)))

    def raw_rows(self, account: str, source: str, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.raw_rows(account,source,reader) if reader else []
        return [self.read_blob(con,r[0]) for r in con.execute("SELECT body_hash FROM analysis_raw WHERE account=? AND source=? ORDER BY occurred_ms",(account,source))]

    def sync_state(self, account: str, source: str, value: dict | None = None) -> dict:
        with self.connect(value is not None) as con:
            if con is None: return {}
            if value is not None:
                con.execute("INSERT OR REPLACE INTO analysis_sync VALUES (?,?,?)",(account,source,json.dumps(sanitize(value),ensure_ascii=False)))
                return value
            r = con.execute("SELECT body_json FROM analysis_sync WHERE account=? AND source=?",(account,source)).fetchone()
            return json.loads(r[0]) if r else {}

    def upsert_trades(self, account: str, trades: list[dict]) -> None:
        with self.connect(True) as con:
            for t in trades:
                t = dict(t)
                prev = con.execute("SELECT body_hash FROM analysis_trades WHERE account=? AND id=?",(account,t["id"])).fetchone()
                if prev:
                    old = self.read_blob(con,prev[0])
                    for k in ("decision_id","config_id","cycle_id","confidence","policy_version","exit_reason","exit_evidence"):
                        if not t.get(k) and old.get(k): t[k] = old[k]
                    if old.get("status") == "closed" and t.get("status") != "closed": continue
                t["account"] = account
                con.execute("""INSERT INTO analysis_trades VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account,id) DO UPDATE SET status=excluded.status,open_ms=excluded.open_ms,
                    close_ms=excluded.close_ms,config_id=excluded.config_id,decision_id=excluded.decision_id,
                    body_hash=excluded.body_hash,updated_ms=excluded.updated_ms""",
                    (account,t["id"],t.get("inst",""),t.get("side",""),t.get("status","unknown"),t.get("open_ms"),t.get("close_ms"),
                     t.get("config_id") or "",t.get("decision_id") or "",self.blob(con,t),now_ms()))

    def trades(self, account: str, filters: dict | None = None, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.trades(account,filters,reader) if reader else []
        filters = filters or {}
        sql, args = "SELECT body_hash FROM analysis_trades WHERE account=?", [account]
        for key in ("inst","side","config_id","status"):
            if filters.get(key) and filters[key] != "all":
                sql += f" AND {key}=?"; args.append(filters[key])
        for key,op in (("start_ms",">="),("end_ms","<")):
            if filters.get(key) is not None:
                sql += f" AND COALESCE(close_ms,open_ms) {op} ?"; args.append(filters[key])
        result = [self.read_blob(con,r[0]) for r in con.execute(sql+" ORDER BY COALESCE(close_ms,open_ms),id",args)]
        if filters.get("result") not in (None,"","all"):
            def matches(t):
                v = decimal(t.get("net_pnl"))
                return v is not None and {"win":v>0,"loss":v<0,"breakeven":v==0}.get(filters["result"],True)
            result = [t for t in result if matches(t)]
        return result

    def events(self, account: str, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.events(account,reader) if reader else []
        return [dict(r) for r in con.execute("SELECT * FROM analysis_events WHERE account=? ORDER BY occurred_ms,id",(account,))]

    def configurations(self, account: str, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.configurations(account,reader) if reader else []
        return [dict(r) for r in con.execute("SELECT * FROM analysis_configs WHERE account=? ORDER BY captured_ms DESC",(account,))]

    def accounts(self) -> list[str]:
        with self.connect() as con:
            if con is None: return []
            return [r[0] for r in con.execute("SELECT account FROM analysis_events UNION SELECT account FROM analysis_trades UNION SELECT account FROM analysis_configs ORDER BY account")]

    def health(self, account: str, con=None) -> dict:
        if con is None:
            with self.connect() as reader:
                if reader: return self.health(account,reader)
                return {"available":False,"bytes":0,"sync":[],"last_observed_ms":None,"coverage_start_ms":None}
        last = con.execute("SELECT MAX(observed_ms),MIN(occurred_ms) FROM analysis_events WHERE account=?",(account,)).fetchone()
        coverage = con.execute("SELECT MIN(open_ms),MAX(close_ms) FROM analysis_trades WHERE account=?",(account,)).fetchone()
        sync = [{"source":r[0],**json.loads(r[1])} for r in con.execute("SELECT source,body_json FROM analysis_sync WHERE account=?",(account,))]
        size = sum(p.stat().st_size for p in (self.path,Path(str(self.path)+"-wal")) if p.exists())
        return {"available":True,"bytes":size,"sync":sync,"last_observed_ms":last[0],
                "coverage_start_ms":coverage[0] or last[1],"coverage_end_ms":coverage[1],
                "coverage_note":"覆盖已归档及交易所可恢复历史；不代表账户全部历史"}

def lifecycle_id(row: dict) -> str:
    # posId alone can survive a reopen. The opening timestamp and direction distinguish lifecycles.
    return "pos_"+digest([row.get("instId"),row.get("posId") or "",row.get("cTime"),row.get("direction") or row.get("posSide")])[:32]

def normalize_position(row: dict, live: bool = False) -> dict:
    opened = timestamp_ms(row.get("cTime"))
    updated = timestamp_ms(row.get("uTime"))
    direction = str(row.get("direction") or row.get("posSide") or "unknown")
    if direction == "net":
        pos = decimal(row.get("pos"))
        direction = "long" if pos and pos > 0 else "short" if pos and pos < 0 else "unknown"
    gross, fee = decimal(row.get("pnl")), decimal(row.get("fee"))
    funding, penalty, settled = (decimal(row.get(k)) for k in ("fundingFee","liqPenalty","settledPnl"))
    realized = decimal(row.get("realizedPnl"))
    components_complete = all(x is not None for x in (gross,fee,funding,penalty,settled))
    net = realized if realized is not None else (gross+fee+funding+penalty+settled if components_complete else None)
    # OKX type 1/4 are partial close/liquidation; only type 2/3/5 are final.
    kind = str(row.get("type",""))
    status = "holding" if live else "partial" if kind in {"1","4"} else "closed" if kind in {"2","3","5"} else "unknown"
    reason = "liquidation" if kind == "3" else "adl" if kind == "5" else None
    def val(v): return str(v) if v is not None else None
    return {
        "id":lifecycle_id(row),"pos_id":row.get("posId"),"inst":str(row.get("instId","")).replace("-USDT-SWAP",""),
        "inst_id":row.get("instId"),"side":direction,"status":status,"open_ms":opened,
        "close_ms":updated if status=="closed" else None,"open_time":time_text(opened),
        "close_time":time_text(updated) if status=="closed" else "",
        "open_px":row.get("avgPx") if live else row.get("openAvgPx"),"close_px":row.get("closeAvgPx"),
        "sz":row.get("pos") if live else (row.get("closeTotalPos") or row.get("openMaxPos")),"lever":row.get("lever"),
        "margin":row.get("margin") or row.get("imr"),"unrealized_pnl":row.get("upl") if live else None,
        "mark_px":row.get("markPx") if live else None,
        "gross_pnl":val(gross),"fee":val(fee),"funding_fee":val(funding),
        "other_settlement":val(penalty+settled) if penalty is not None and settled is not None else None,
        "net_pnl":val(net) if not live else None,"pnl":val(net) if not live else None,
        # Exchange realizedPnl is authoritative for the net result, but it does
        # not prove that fee/funding/liquidation components were all captured.
        # Keep those two facts separate so the completeness percentage cannot
        # look perfect merely because one aggregate field exists.
        "cost_complete":components_complete and net is not None and not live,
        "cost_basis":"exchange_realizedPnl" if realized is not None else "complete_components" if components_complete else "incomplete",
        "components_complete":components_complete,
        "known_net_pnl":val(gross+fee) if gross is not None and fee is not None else None,
        "exit_reason":reason,"exit_evidence":"confirmed" if reason else "unknown",
        "strategy":"unknown","source":"okx_positions","raw_type":kind,
    }
