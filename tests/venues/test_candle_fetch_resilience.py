"""Candle reads recover from transient failures without weakening closed-bar checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from unittest.mock import MagicMock, patch

import pytest
import requests

from scripts import market_data_service as mds


def response(code="0", data=None, status=200):
    result = MagicMock()
    result.status_code = status
    result.json.return_value = {"code": code, "data": data or []}
    return result


@pytest.mark.parametrize("failure", [
    response(code="50011"),
    response(status=429),
    response(),
    requests.ConnectionError("connection reset"),
])
def test_primary_retry_recovers_without_contacting_backup(failure):
    session = MagicMock()
    closed = ["1", "10", "11", "9", "10", "2", "2", "20", "1"]
    forming = closed[:8] + ["0"]
    session.get.side_effect = [failure, response(data=[forming, closed])]
    with patch.object(mds, "get_market_session", return_value=session), \
         patch.object(mds, "_throttle_candles"), patch.object(mds.time, "sleep"):
        rows = mds.fetch_candles("BTC-USDT-SWAP", bar="1H", limit=1)
    assert rows == [closed]
    assert session.get.call_count == 2
    assert all(call.args[0].startswith(mds.OKX_PUBLIC_HOSTS[0])
               for call in session.get.call_args_list)


def test_persistent_rate_limit_is_bounded_and_returns_missing():
    session = MagicMock()
    session.get.return_value = response(code="50011")
    with patch.object(mds, "get_market_session", return_value=session), \
         patch.object(mds, "_throttle_candles"), patch.object(mds.time, "sleep"):
        assert mds.fetch_candles("BTC-USDT-SWAP", bar="1H") == []
    assert session.get.call_count == 3 * len(mds.OKX_PUBLIC_HOSTS)


def test_invalid_parameters_are_not_retried_on_the_same_host():
    session = MagicMock()
    session.get.return_value = response(code="51000")
    with patch.object(mds, "get_market_session", return_value=session), \
         patch.object(mds, "_throttle_candles"), patch.object(mds.time, "sleep"):
        assert mds.fetch_candles("BTC-USDT-SWAP", bar="bad") == []
    assert session.get.call_count == len(mds.OKX_PUBLIC_HOSTS)


def test_failed_primary_still_uses_existing_backup():
    session = MagicMock()
    session.get.side_effect = [response(code="50011")] * 3 + [response(data=[["closed"]])]
    with patch.object(mds, "get_market_session", return_value=session), \
         patch.object(mds, "_throttle_candles"), patch.object(mds.time, "sleep"):
        payload = mds._public_get("/api/v5/market/candles", {"instId": "BTC-USDT-SWAP", "bar": "1H"})
    assert payload["data"] == [["closed"]]
    assert session.get.call_args_list[-1].args[0].startswith(mds.OKX_PUBLIC_HOSTS[1])


def test_parallel_callers_reserve_separate_candle_slots():
    clock = [100.0]
    starts = []
    record_lock = Lock()

    def sleep(delay):
        clock[0] += delay

    def take_slot(_):
        mds._throttle_candles()
        # Slot reservation timestamps are recorded by the clock mock below.

    def now():
        with record_lock:
            starts.append(clock[0])
        return clock[0]

    with patch.object(mds, "_CANDLE_LAST_STARTED", 0.0), \
         patch.object(mds.time, "monotonic", now), patch.object(mds.time, "sleep", sleep):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(take_slot, range(8)))
    reserved = starts[1::2]
    assert len(reserved) == 8
    assert all(b - a >= mds._CANDLE_MIN_INTERVAL - 1e-9
               for a, b in zip(reserved, reserved[1:]))
