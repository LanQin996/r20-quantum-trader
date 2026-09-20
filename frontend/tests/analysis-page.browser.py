"""Browser regression for the analysis page using an isolated, mocked API.

Run: python -X utf8 frontend/tests/analysis-page.browser.py
Requires the optional test dependency: pip install playwright
Install a Playwright Chromium browser first, or set PLAYWRIGHT_CHROMIUM_EXECUTABLE.
No backend is started and all /api requests are intercepted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

FRONTEND = Path(__file__).resolve().parents[1]
ACCOUNT = "okx:live:browser-fixture"
CONFIG = {"id": "fixture-config", "source": "runtime", "process": "trader", "captured_ms": 1789866000000}
OUTPUT = Path(tempfile.gettempdir()) / "r20-analysis-browser"
OUTPUT.mkdir(exist_ok=True)


def run():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    node = shutil.which("node")
    assert node, "Node.js is required"
    server = subprocess.Popen(
        [node, str(FRONTEND / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1",
         "--port", str(port), "--strictPort"],
        cwd=FRONTEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    requests = []
    errors = []
    export_state = {"starts": 0, "ready": False, "cancelled": False}
    def mock_api(route):
        url = urlparse(route.request.url)
        query = parse_qs(url.query)
        requests.append((url.path, query))
        account = query.get("account", [ACCOUNT])[0] or ACCOUNT
        trade = {
            "id": "fixture-trade", "inst": "SOL", "side": "short", "cost_complete": True,
            "status": "closed", "net_pnl": "0.78", "fee": "-0.01", "funding_fee": "0",
            "close_time": "2026-09-20 11:00:00", "exit_reason": "分批止盈", "exit_evidence": "confirmed",
            "decision_id": "fixture-decision",
        }
        event = {"id": "fixture-event", "kind": "order.submitted", "status": "accepted",
                 "occurred_ms": 1789866000000, "inst": "SOL"}
        root = "/api/v1/admin/analysis"
        if url.path.endswith("/auth/me"):
            data = {"user": {"username": "browser-fixture", "role": "superadmin"}}
        elif url.path == root + "/summary":
            data = {
                "account": account, "accounts": [ACCOUNT, "okx:demo:browser-fixture"],
                "instruments": ["SOL"], "configurations": [CONFIG],
                "statistics": {
                    "closed_count": 173, "sample_count": 173, "wins": 93, "losses": 80,
                    "breakeven": 0, "incomplete_count": 0, "win_rate": 53.8,
                    "win_rate_ci95": [46.3, 61], "net_pnl": 42.5, "profit_factor": 1.4,
                    "payoff_ratio": 1.2, "avg_win": 2.4, "avg_loss": -2.0,
                    "expectancy": .25, "longest_loss_streak": 3, "max_realized_drawdown": 6.2,
                    "fee": -4.2, "funding_fee": .1, "decision_link_rate": 96.5,
                    "cost_complete_rate": 100,
                    "curve": [{"time_ms": 1789865000000, "net": -2, "drawdown": 2, "trade_id": "t0"},
                              {"time_ms": 1789866000000, "net": 42.5, "drawdown": 0, "trade_id": "t1"}],
                },
                "health": {"coverage_start_ms": 1789865000000, "last_observed_ms": 1789866000000,
                           "bytes": 780000000, "sync": [{"source": "fills", "complete": True}]},
                "execution": {"proposed_decisions": 10, "passed_decisions": 8,
                              "submitted_orders": 6, "filled_orders": 5},
            }
        elif url.path == root + "/trades":
            data = {"items": [dict(trade, inst="SOL" if query.get("page", ["1"])[0] == "1" else "SOL-PAGE-2")], "total": 21}
        elif url.path == root + "/breakdown":
            data = {"items": [{"key": "SOL_GROUP", "sample_count": 173, "incomplete_count": 0,
                               "win_rate": 53.8, "net_pnl": 42.5, "profit_factor": 1.4, "expectancy": .25}]}
        elif url.path == root + "/events":
            data = {"items": [event], "total": 31}
        elif url.path == root + "/trades/fixture-trade":
            data = {"trade": trade, "events": [event], "config_ids": [CONFIG["id"]]}
        elif url.path == root + "/events/fixture-event":
            data = {**event, "body": {"fixture_evidence": "confirmed"}}
        elif "/configurations/" in url.path:
            current = url.path.endswith("/current")
            data = {**CONFIG, "id": "current" if current else CONFIG["id"],
                    "body": {"risk_limit": 20 if current else 10}}
        elif url.path == root + "/exports":
            export_state["starts"] += 1
            export_state.update(ready=False, cancelled=False)
            data = {"id": f"fixture-export-{export_state['starts']}", "state": "running",
                    "stage": "events", "completed": 128, "total": 50000, "bytes": 1000, "error": ""}
        elif url.path.endswith("/download") and "/exports/" in url.path:
            data = {"url": url.path.removesuffix("/download") + "/file"}
        elif url.path.endswith("/file") and "/exports/" in url.path:
            route.fulfill(status=200, content_type="application/zip",
                          headers={"Content-Disposition": 'attachment; filename="r20-analysis.zip"'},
                          body=b"PK\x05\x06" + b"\0" * 18)
            return
        elif "/exports/" in url.path:
            if route.request.method == "DELETE":
                export_state["cancelled"] = True
            state = "cancelled" if export_state["cancelled"] else "ready" if export_state["ready"] else "running"
            data = {"id": url.path.rsplit("/", 1)[1], "state": state, "stage": "events",
                    "completed": 128, "total": 50000, "bytes": 1234567, "error": ""}
        else:
            data = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    try:
        for _ in range(100):
            try:
                with urlopen(base, timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError("Vite did not start")
        with sync_playwright() as playwright:
            executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
            if not executable and os.name == "nt":
                cached = sorted((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob(
                    "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"))
                executable = str(cached[-1]) if cached else None
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            context.add_init_script("""
                localStorage.setItem('r20.admin.session.id', 'browser-fixture');
                localStorage.setItem('r20.admin.session.user', JSON.stringify({username:'fixture',role:'superadmin'}));
                localStorage.setItem('r20_theme_v2', 'light');
            """)
            context.route(lambda url: urlparse(url).path.startswith("/api/"), mock_api)
            page = context.new_page()
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(base + "/admin/analysis")
            form = page.get_by_role("form", name="应用筛选")
            expect(form).to_have_attribute("aria-busy", "false")
            expect(page.get_by_text("53.8%", exact=True)).to_be_visible()
            assert "analysis.minimum" not in page.locator("body").inner_text()
            form_box = form.bounding_box()
            assert form_box["height"] < 200, form_box
            fields = form.locator("label")
            assert fields.nth(0).bounding_box()["x"] < fields.nth(1).bounding_box()["x"]
            page.screenshot(path=str(OUTPUT / "desktop-light.png"), full_page=True)

            # Lazy tabs must load on their first click, without pressing refresh.
            assert not any(path.endswith("/breakdown") for path, _ in requests)
            page.get_by_role("tab", name="交易归因", exact=True).click()
            expect(page.get_by_role("cell", name="SOL_GROUP", exact=True)).to_be_visible()
            page.get_by_role("button", name="下一页", exact=True).click()
            expect(page.get_by_role("cell", name="SOL-PAGE-2", exact=True)).to_be_visible()
            page.get_by_role("button", name="逐笔复盘", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            page.keyboard.press("Escape")
            assert not any(path.endswith("/events") for path, _ in requests)
            page.get_by_role("tab", name="风控与执行", exact=True).click()
            expect(page.get_by_role("cell", name="accepted", exact=True)).to_be_visible()
            page.get_by_role("button", name="查看原始证据", exact=True).click()
            expect(page.get_by_role("dialog")).to_contain_text("fixture_evidence")
            page.keyboard.press("Escape")
            page.get_by_role("tab", name="提示词与配置", exact=True).click()
            expect(page.get_by_role("cell", name="risk_limit", exact=True)).to_be_visible()
            assert page.locator(".analysis-compare").bounding_box()["height"] < 120

            # Edited controls take effect together on submit, including the account.
            form.get_by_label("账户 / 环境").select_option("okx:demo:browser-fixture")
            form.get_by_label("币种", exact=True).select_option("SOL")
            form.get_by_role("button", name="应用筛选").click()
            expect(form).to_have_attribute("aria-busy", "false")
            assert any(path.endswith("/summary") and query.get("account") == ["okx:demo:browser-fixture"]
                       and query.get("inst") == ["SOL"] for path, query in requests)
            page.get_by_role("button", name="导出完整分析包").click()
            expect(page.get_by_text("正在写入事件证据", exact=False)).to_be_visible()
            page.reload()
            expect(page.get_by_text("正在写入事件证据", exact=False)).to_be_visible()
            assert export_state["starts"] == 1
            with page.expect_download() as downloaded:
                export_state["ready"] = True
                page.get_by_role("button", name="重新下载").wait_for()
            assert downloaded.value.suggested_filename.endswith(".zip")
            with page.expect_download():
                page.get_by_role("button", name="重新下载").click()
            assert export_state["starts"] == 1, "Retrying a download must not regenerate the bundle"
            page.get_by_role("button", name="导出完整分析包").click()
            page.get_by_role("button", name="取消导出").click()
            expect(page.get_by_text("导出已取消", exact=True)).to_be_visible()
            page.get_by_role("tab", name="收益总览", exact=True).click()

            for width, theme in [(1440, "dark"), (768, "light"), (390, "light")]:
                page.set_viewport_size({"width": width, "height": 1000})
                page.evaluate("(theme) => document.documentElement.setAttribute('data-theme', theme)", theme)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                for field in fields.all():
                    box = field.bounding_box()
                    assert box["width"] > 110 and box["x"] >= 0 and box["x"] + box["width"] <= width, box
                page.screenshot(path=str(OUTPUT / f"{width}-{theme}.png"), full_page=True)
            assert not errors, errors
            browser.close()
        print(json.dumps({"result": "passed", "checks": [
            "desktop/tablet/mobile layout", "light/dark themes", "lazy attribution/execution tabs",
            "pagination", "trade/event drawers", "configuration comparison", "filters",
            "background export progress", "resume after reload", "native download and retry", "cancel export",
        ], "screenshots": str(OUTPUT)}, ensure_ascii=False))
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    run()
