#!/usr/bin/env python3
"""Screen Shanghai main-board stocks for IPO quota base-holding candidates."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable


SHANGHAI_MAIN_BOARD_PREFIXES = ("600", "601", "603", "605")
DEFAULT_MIN_HISTORY_DAYS = 120
DEFAULT_MIN_AVG_AMOUNT = 100_000_000
DEFAULT_MIN_MARKET_CAP = 20_000_000_000
DEFAULT_TOP = 30
DEFAULT_PROVIDER = "baostock"
DEFAULT_UNIVERSE = "large_cap"
DEFAULT_BASKET_SIZE = 8
DEFAULT_FINAL_PER_BASKET = 2
RETURN_PERIODS = (5, 20, 60, 120)


@dataclass
class ScreeningResult:
    rows: list[dict[str, Any]]
    rejected: list[dict[str, str]]


def normalize_code(code: Any) -> str:
    value = str(code or "").strip().upper()
    if "." in value:
        left, right = value.split(".", 1)
        value = right if left in ("SH", "SZ") else left
    elif value.startswith(("SH", "SZ")):
        value = value[2:]
    return value.zfill(6) if value.isdigit() else value


def is_shanghai_main_board(code: Any) -> bool:
    return normalize_code(code).startswith(SHANGHAI_MAIN_BOARD_PREFIXES)


def is_st_name(name: Any) -> bool:
    normalized = str(name or "").upper().replace(" ", "")
    risk_tokens = ("ST", "*ST", "退", "退市", "PT")
    return any(token in normalized for token in risk_tokens)


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "" or value == "-":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def calculate_metrics(history: list[dict[str, Any]]) -> dict[str, float | None]:
    closes = [safe_float(row.get("close")) for row in history]
    amounts = [safe_float(row.get("amount"), 0.0) for row in history]
    closes = [value for value in closes if value is not None and value > 0]
    amounts = [value or 0.0 for value in amounts]

    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]

    def realized_volatility(days: int) -> float:
        window = returns[-days:]
        if len(window) < 2:
            return 0.0
        mean = sum(window) / len(window)
        variance = sum((item - mean) ** 2 for item in window) / (len(window) - 1)
        return math.sqrt(variance) * math.sqrt(252)

    def max_drawdown(days: int) -> float:
        window = closes[-days:]
        if not window:
            return 0.0
        peak = window[0]
        worst = 0.0
        for price in window:
            peak = max(peak, price)
            if peak > 0:
                worst = min(worst, price / peak - 1)
        return abs(worst)

    def average_amount(days: int) -> float:
        window = amounts[-days:]
        if not window:
            return 0.0
        return sum(window) / len(window)

    def period_return(days: int) -> float | None:
        if len(closes) <= days:
            return None
        start = closes[-days - 1]
        end = closes[-1]
        if start <= 0 or end <= 0:
            return None
        return end / start - 1

    metrics: dict[str, float | None] = {
        "history_days": float(len(closes)),
        "latest_close": closes[-1] if closes else 0.0,
        "avg_amount_20d": average_amount(20),
        "avg_amount_60d": average_amount(60),
        "volatility_60d": realized_volatility(60),
        "volatility_120d": realized_volatility(120),
        "max_drawdown_120d": max_drawdown(120),
    }
    for days in RETURN_PERIODS:
        metrics[f"return_{days}d"] = period_return(days)
    return metrics


def percentile_scores(values: list[float], higher_is_better: bool) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [100.0]
    if len(set(values)) == 1:
        return [50.0] * len(values)

    indexed = sorted(enumerate(values), key=lambda item: item[1])
    scores = [0.0] * len(values)
    rank = 0
    while rank < len(indexed):
        end = rank + 1
        while end < len(indexed) and indexed[end][1] == indexed[rank][1]:
            end += 1
        midpoint_rank = (rank + end - 1) / 2
        percentile = midpoint_rank / (len(values) - 1) * 100
        for index, _ in indexed[rank:end]:
            scores[index] = percentile if higher_is_better else 100 - percentile
        rank = end
    return scores


def assign_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows

    score_specs = [
        ("avg_amount_20d", True, 0.20),
        ("total_market_cap", True, 0.15),
        ("volatility_120d", False, 0.30),
        ("max_drawdown_120d", False, 0.15),
        ("dividend_yield", True, 0.20),
    ]
    total_scores = [0.0] * len(rows)

    for field, higher_is_better, weight in score_specs:
        values = [safe_float(row.get(field), 0.0) or 0.0 for row in rows]
        scores = percentile_scores(values, higher_is_better)
        for index, score in enumerate(scores):
            total_scores[index] += score * weight

    for row, score in zip(rows, total_scores):
        row["score"] = round(score, 2)
        row["rating"] = rating_for_score(row["score"])
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def rating_for_score(score: float) -> str:
    if score >= 70:
        return "候选"
    if score >= 45:
        return "观察"
    return "谨慎"


BASKET_SPECS = {
    "大盘核心": {
        "total_market_cap": (True, 0.30),
        "avg_amount_20d": (True, 0.25),
        "volatility_120d": (False, 0.20),
        "max_drawdown_120d": (False, 0.15),
        "dividend_yield": (True, 0.10),
    },
    "稳健底仓": {
        "avg_amount_20d": (True, 0.20),
        "total_market_cap": (True, 0.15),
        "volatility_120d": (False, 0.30),
        "max_drawdown_120d": (False, 0.25),
        "roe": (True, 0.10),
    },
    "高股息价值": {
        "dividend_yield": (True, 0.35),
        "volatility_120d": (False, 0.20),
        "max_drawdown_120d": (False, 0.15),
        "pb": (False, 0.10),
        "roe": (True, 0.10),
        "avg_amount_20d": (True, 0.10),
    },
    "金融低波": {
        "dividend_yield": (True, 0.25),
        "volatility_120d": (False, 0.25),
        "max_drawdown_120d": (False, 0.20),
        "pb": (False, 0.15),
        "avg_amount_20d": (True, 0.15),
    },
    "公用事业基建": {
        "volatility_120d": (False, 0.25),
        "max_drawdown_120d": (False, 0.20),
        "dividend_yield": (True, 0.20),
        "cfo_to_np": (True, 0.15),
        "avg_amount_20d": (True, 0.10),
        "roe": (True, 0.10),
    },
    "低回撤观察": {
        "max_drawdown_120d": (False, 0.35),
        "volatility_120d": (False, 0.25),
        "avg_amount_20d": (True, 0.20),
        "total_market_cap": (True, 0.10),
        "roe": (True, 0.10),
    },
}


def build_baskets(
    rows: list[dict[str, Any]],
    *,
    per_basket: int = 10,
    max_per_industry: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    baskets: dict[str, list[dict[str, Any]]] = {}
    for basket_name, score_spec in BASKET_SPECS.items():
        scored = score_for_spec(rows, score_spec, f"{basket_name}_score")
        baskets[basket_name] = select_with_industry_limit(
            scored,
            score_field=f"{basket_name}_score",
            limit=per_basket,
            max_per_industry=max_per_industry,
            basket_name=basket_name,
        )
    return baskets


def build_final_picks(
    baskets: dict[str, list[dict[str, Any]]],
    *,
    per_basket: int = DEFAULT_FINAL_PER_BASKET,
) -> list[dict[str, Any]]:
    selected_codes: set[str] = set()
    final_rows: list[dict[str, Any]] = []
    for basket_name, rows in baskets.items():
        picked = 0
        for row in rows:
            code = normalize_code(row.get("code"))
            if not code or code in selected_codes:
                continue
            selected = dict(row)
            selected["source_basket"] = basket_name
            selected["final_pick_order"] = len(final_rows) + 1
            final_rows.append(selected)
            selected_codes.add(code)
            picked += 1
            if picked >= per_basket:
                break
    return final_rows


def build_basket_return_summary(baskets: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for basket_name, rows in baskets.items():
        row: dict[str, Any] = {"basket": basket_name, "count": len(rows)}
        for days in RETURN_PERIODS:
            row[f"return_{days}d"] = average_field(rows, f"return_{days}d")
        summary.append(row)
    return summary


def average_field(rows: Iterable[dict[str, Any]], field: str) -> float | None:
    values = [safe_float(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def score_for_spec(
    rows: list[dict[str, Any]],
    score_spec: dict[str, tuple[bool, float]],
    score_field: str,
) -> list[dict[str, Any]]:
    scored = [dict(row) for row in rows]
    totals = [0.0] * len(scored)
    for field, (higher_is_better, weight) in score_spec.items():
        values = [safe_float(row.get(field), 0.0) or 0.0 for row in scored]
        scores = percentile_scores(values, higher_is_better)
        for index, score in enumerate(scores):
            totals[index] += score * weight
    for row, score in zip(scored, totals):
        row[score_field] = round(score, 2)
    return sorted(scored, key=lambda item: item.get(score_field, 0), reverse=True)


def select_with_industry_limit(
    rows: list[dict[str, Any]],
    *,
    score_field: str,
    limit: int,
    max_per_industry: int,
    basket_name: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    industry_counts: dict[str, int] = {}
    for row in rows:
        if not basket_allows_row(basket_name, row):
            continue
        industry = normalize_industry(row.get("industry"))
        if industry_counts.get(industry, 0) >= max_per_industry:
            continue
        selected_row = dict(row)
        selected_row["basket_score"] = row.get(score_field)
        selected.append(selected_row)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def basket_allows_row(basket_name: str, row: dict[str, Any]) -> bool:
    industry = str(row.get("industry") or "")
    if basket_name == "金融低波":
        return "金融" in industry or "保险" in industry or "资本市场" in industry
    if basket_name == "公用事业基建":
        keywords = ("电力", "燃气", "水", "铁路", "公路", "港口", "机场", "建筑", "基建", "运输")
        return any(keyword in industry or keyword in str(row.get("name") or "") for keyword in keywords)
    return True


def normalize_industry(industry: Any) -> str:
    value = str(industry or "未知")
    return value[:3] if len(value) >= 3 else value


def screen_candidates(
    stocks: Iterable[dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    *,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
    min_avg_amount: float = DEFAULT_MIN_AVG_AMOUNT,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
) -> ScreeningResult:
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for stock in stocks:
        code = normalize_code(stock.get("code"))
        name = str(stock.get("name") or "")
        reasons: list[str] = []

        if not is_shanghai_main_board(code):
            reasons.append("not_shanghai_main_board")
        if is_st_name(name):
            reasons.append("st_or_delisting_risk")

        market_cap = safe_float(stock.get("total_market_cap"))
        market_cap_missing = market_cap is None or market_cap <= 0
        if not market_cap_missing and market_cap < min_market_cap:
            reasons.append("market_cap_too_small")

        metrics = calculate_metrics(histories.get(code, []))
        if metrics["history_days"] < min_history_days:
            reasons.append("insufficient_history")
        if metrics["avg_amount_20d"] < min_avg_amount:
            reasons.append("liquidity_too_low")

        if reasons:
            rejected.append({"code": code, "name": name, "reason": ",".join(reasons)})
            continue

        row = {
            "code": code,
            "name": name,
            "industry": stock.get("industry", ""),
            "total_market_cap": market_cap,
            "float_market_cap": safe_float(stock.get("float_market_cap")),
            "market_cap_checked": not market_cap_missing,
            "pe": latest_history_value(histories.get(code, []), "pe", stock.get("pe")),
            "pb": latest_history_value(histories.get(code, []), "pb", stock.get("pb")),
            "dividend_yield": safe_float(stock.get("dividend_yield"), 0.0) or 0.0,
            "dividend_cash_per_share": safe_float(stock.get("dividend_cash_per_share")),
            "dividend_years_3y": safe_float(stock.get("dividend_years_3y")),
            "roe": safe_float(stock.get("roe")),
            "net_profit_margin": safe_float(stock.get("net_profit_margin")),
            "gross_margin": safe_float(stock.get("gross_margin")),
            "debt_to_asset": safe_float(stock.get("debt_to_asset")),
            "current_ratio": safe_float(stock.get("current_ratio")),
            "asset_to_equity": safe_float(stock.get("asset_to_equity")),
            "cfo_to_np": safe_float(stock.get("cfo_to_np")),
            "cfo_to_revenue": safe_float(stock.get("cfo_to_revenue")),
            "quality_stat_date": stock.get("quality_stat_date"),
            **metrics,
        }
        rows.append(row)

    return ScreeningResult(rows=assign_scores(rows), rejected=rejected)


def latest_history_value(history: list[dict[str, Any]], field: str, default: Any = None) -> float | None:
    for row in reversed(history):
        value = safe_float(row.get(field))
        if value is not None:
            return value
    return safe_float(default)


def apply_history_enrichment(stocks: list[dict[str, Any]], histories: dict[str, list[dict[str, Any]]]) -> None:
    by_code = {normalize_code(stock.get("code")): stock for stock in stocks}
    for code, history in histories.items():
        stock = by_code.get(normalize_code(code))
        if not stock:
            continue
        metrics = calculate_metrics(history)
        latest_close = metrics.get("latest_close", 0.0)
        total_share = latest_history_value(history, "total_share")
        float_share = latest_history_value(history, "float_share")
        if latest_close and total_share:
            stock["total_market_cap"] = latest_close * total_share
        if latest_close and float_share:
            stock["float_market_cap"] = latest_close * float_share
        stock["pe"] = latest_history_value(history, "pe", stock.get("pe"))
        stock["pb"] = latest_history_value(history, "pb", stock.get("pb"))


def apply_fundamental_enrichment(
    stocks: list[dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
    enrichments: dict[str, dict[str, Any]],
) -> None:
    by_code = {normalize_code(stock.get("code")): stock for stock in stocks}
    for code, enrichment in enrichments.items():
        stock = by_code.get(normalize_code(code))
        if not stock:
            continue
        stock.update({key: value for key, value in enrichment.items() if key != "code"})
        history = histories.get(normalize_code(code), [])
        latest_close = calculate_metrics(history).get("latest_close", 0.0)
        total_share = safe_float(stock.get("total_share"))
        float_share = safe_float(stock.get("float_share"))
        cash_per_share = safe_float(stock.get("dividend_cash_per_share"), 0.0) or 0.0
        if latest_close and total_share:
            stock["total_market_cap"] = latest_close * total_share
        if latest_close and float_share:
            stock["float_market_cap"] = latest_close * float_share
        if latest_close and cash_per_share:
            stock["dividend_yield"] = cash_per_share / latest_close * 100


def stocks_from_codes(codes: str) -> list[dict[str, Any]]:
    stocks: list[dict[str, Any]] = []
    for raw_code in codes.split(","):
        code = normalize_code(raw_code)
        if not code:
            continue
        stocks.append(
            {
                "code": code,
                "name": code,
                "total_market_cap": None,
                "float_market_cap": None,
                "pe": None,
                "pb": None,
                "source": "manual_codes",
            }
        )
    return stocks


def market_cap_passes_prefilter(value: Any, min_market_cap: float) -> bool:
    market_cap = safe_float(value)
    return market_cap is None or market_cap <= 0 or market_cap >= min_market_cap


@dataclass
class DataProvider:
    fetch_stock_list: Callable[[str], list[dict[str, Any]]]
    fetch_history: Callable[[str, str, str], list[dict[str, Any]]]
    label: str
    close: Callable[[], None] | None = None
    fetch_enrichment: Callable[[str, str, str], dict[str, Any]] | None = None


def get_data_provider(provider: str) -> DataProvider:
    if provider == "baostock":
        fetch_stock_list, fetch_history, fetch_enrichment, close = get_baostock_provider()
        return DataProvider(
            fetch_stock_list=fetch_stock_list,
            fetch_history=fetch_history,
            label="Baostock",
            close=close,
            fetch_enrichment=fetch_enrichment,
        )
    if provider == "akshare":
        fetch_stock_list, fetch_history = get_akshare_provider()
        return DataProvider(fetch_stock_list=fetch_stock_list, fetch_history=fetch_history, label="AKShare")
    raise ValueError(f"Unsupported provider: {provider}")


def get_baostock_provider() -> tuple[
    Callable[[str], list[dict[str, Any]]],
    Callable[[str, str, str], list[dict[str, Any]]],
    Callable[[str, str, str], dict[str, Any]],
    Callable[[], None],
]:
    try:
        import baostock as bs
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "Baostock is not installed. Run `uv sync` inside stock_analysis_a_stock first."
        ) from exc

    def result_set_to_frame(rs: Any) -> Any:
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=rs.fields)

    logged_in = False

    def ensure_login() -> Any:
        nonlocal logged_in
        if logged_in:
            return bs
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"Baostock login failed: {lg.error_msg}")
        logged_in = True
        return bs

    def logout() -> None:
        nonlocal logged_in
        if not logged_in:
            return
        try:
            bs.logout()
        except Exception:
            pass
        finally:
            logged_in = False

    def stock_basic_map() -> dict[str, dict[str, Any]]:
        ensure_login()
        rs = bs.query_stock_basic()
        df = result_set_to_frame(rs)

        stocks: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            code = normalize_code(row.get("code"))
            if not is_shanghai_main_board(code):
                continue
            if str(row.get("type", "")) != "1":
                continue
            if str(row.get("status", "")) != "1":
                continue
            stocks[code] = make_stock_record(code, row.get("code_name", ""), "baostock")
        return stocks

    def fetch_stock_list(universe: str = DEFAULT_UNIVERSE) -> list[dict[str, Any]]:
        stocks_by_code = stock_basic_map()
        if universe == "all":
            return list(stocks_by_code.values())

        ensure_login()
        selected_codes: set[str] = set()
        if universe in ("large_cap", "sz50"):
            rs = bs.query_sz50_stocks()
            selected_codes.update(codes_from_result_set(result_set_to_frame(rs)))
        if universe in ("large_cap", "hs300"):
            rs = bs.query_hs300_stocks()
            selected_codes.update(codes_from_result_set(result_set_to_frame(rs)))
        if universe == "zz500":
            rs = bs.query_zz500_stocks()
            selected_codes.update(codes_from_result_set(result_set_to_frame(rs)))

        if not selected_codes:
            raise RuntimeError(f"No stocks found for universe: {universe}")

        return [
            stocks_by_code[code]
            for code in sorted(selected_codes)
            if code in stocks_by_code and is_shanghai_main_board(code)
        ]
        return stocks

    def fetch_history(code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        bs_code = baostock_code(code)
        ensure_login()
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,open,high,low,close,volume,amount,pbMRQ,peTTM",
            start_date=date_for_baostock(start_date),
            end_date=date_for_baostock(end_date),
            frequency="d",
            adjustflag="3",
        )
        df = result_set_to_frame(rs)

        history = normalize_history_frame(df)
        return history

    def fetch_enrichment(code: str, start_date: str, end_date: str) -> dict[str, Any]:
        normalized = normalize_code(code)
        bs_code = baostock_code(normalized)
        ensure_login()
        industry = fetch_baostock_industry(bs, result_set_to_frame, bs_code)
        quality = fetch_baostock_quality(bs, result_set_to_frame, bs_code)
        dividends = fetch_baostock_dividends(bs, result_set_to_frame, bs_code, end_date)
        return {
            "code": normalized,
            "industry": industry,
            **quality,
            **dividends,
        }

    return fetch_stock_list, fetch_history, fetch_enrichment, logout


def baostock_code(code: Any) -> str:
    normalized = normalize_code(code)
    prefix = "sh" if normalized.startswith(("6", "9")) else "sz"
    return f"{prefix}.{normalized}"


def codes_from_result_set(df: Any) -> set[str]:
    codes: set[str] = set()
    for _, row in df.iterrows():
        code = normalize_code(first_present(row, ["code", "证券代码"]))
        if code:
            codes.add(code)
    return codes


def make_stock_record(code: str, name: Any, source: str) -> dict[str, Any]:
    return {
        "code": normalize_code(code),
        "name": name or normalize_code(code),
        "total_market_cap": None,
        "float_market_cap": None,
        "pe": None,
        "pb": None,
        "source": source,
    }


def date_for_baostock(value: str) -> str:
    value = str(value)
    if "-" in value:
        return value
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def get_akshare_provider() -> tuple[Callable[[], list[dict[str, Any]]], Callable[[str, str, str], list[dict[str, Any]]]]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AKShare is not installed. Run `uv sync` inside stock_analysis_a_stock first."
        ) from exc

    def fetch_stock_list(universe: str = DEFAULT_UNIVERSE) -> list[dict[str, Any]]:
        try:
            df = retry_call(ak.stock_zh_a_spot_em, label="stock_zh_a_spot_em")
            source = "eastmoney"
        except Exception as exc:
            print(f"[warn] stock_zh_a_spot_em unavailable, falling back to stock_zh_a_spot: {exc}", file=sys.stderr)
            df = None
            source = "legacy"
        if df is None or df.empty:
            try:
                df = retry_call(ak.stock_zh_a_spot, label="stock_zh_a_spot")
                source = "legacy"
            except Exception as exc:
                print(
                    f"[warn] stock_zh_a_spot unavailable, falling back to SSE main-board code list: {exc}",
                    file=sys.stderr,
                )
                return fetch_shanghai_main_board_list(ak)
        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            stocks.append(
                {
                    "code": normalize_code(first_present(row, ["代码", "code"])),
                    "name": first_present(row, ["名称", "name"], ""),
                    "total_market_cap": safe_float(first_present(row, ["总市值", "mktcap"])),
                    "float_market_cap": safe_float(first_present(row, ["流通市值", "nmc"])),
                    "pe": safe_float(first_present(row, ["市盈率-动态", "市盈率", "pe"])),
                    "pb": safe_float(first_present(row, ["市净率", "pb"])),
                    "source": source,
                }
            )
        return stocks

    def fetch_history(code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        normalized_code = normalize_code(code)
        try:
            df = retry_call(
                ak.stock_zh_a_hist,
                label=f"stock_zh_a_hist:{code}",
                symbol=normalized_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
            return normalize_history_frame(df)
        except Exception as exc:
            print(f"[warn] stock_zh_a_hist unavailable for {code}, falling back to stock_zh_a_daily: {exc}", file=sys.stderr)

        df = retry_call(
            ak.stock_zh_a_daily,
            label=f"stock_zh_a_daily:{code}",
            symbol=market_prefixed_code(normalized_code),
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
        return normalize_history_frame(df)

    return fetch_stock_list, fetch_history


def normalize_history_frame(df: Any) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        history.append(
                {
                    "date": str(first_present(row, ["日期", "date"], "")),
                    "close": safe_float(first_present(row, ["收盘", "close"])),
                    "amount": safe_float(first_present(row, ["成交额", "amount"]), 0.0) or 0.0,
                    "pb": safe_float(first_present(row, ["市净率", "pb", "pbMRQ"])),
                    "pe": safe_float(first_present(row, ["市盈率-动态", "市盈率", "pe", "peTTM"])),
                }
            )
    return history


def market_prefixed_code(code: Any) -> str:
    normalized = normalize_code(code)
    prefix = "sh" if normalized.startswith(("6", "9")) else "sz"
    return f"{prefix}{normalized}"


def fetch_baostock_industry(bs: Any, to_frame: Callable[[Any], Any], bs_code: str) -> str:
    try:
        rs = bs.query_stock_industry(code=bs_code, date="")
        df = to_frame(rs)
    except Exception:
        return ""
    if df.empty:
        return ""
    return str(first_present(df.iloc[0], ["industry"], ""))


def fetch_baostock_quality(bs: Any, to_frame: Callable[[Any], Any], bs_code: str) -> dict[str, Any]:
    current_year = datetime.now().year
    quarters = [(year, quarter) for year in range(current_year, current_year - 3, -1) for quarter in (4, 3, 2, 1)]
    quarters = quarters[:8]
    quality: dict[str, Any] = {}
    for year, quarter in quarters:
        if quality:
            break
        try:
            profit_df = to_frame(bs.query_profit_data(code=bs_code, year=year, quarter=quarter))
        except Exception:
            profit_df = None
        if profit_df is not None and not profit_df.empty:
            row = profit_df.iloc[0]
            quality.update(
                {
                    "roe": pct_value(row.get("roeAvg")),
                    "net_profit_margin": pct_value(row.get("npMargin")),
                    "gross_margin": pct_value(row.get("gpMargin")),
                    "eps_ttm": safe_float(row.get("epsTTM")),
                    "net_profit": safe_float(row.get("netProfit")),
                    "revenue": safe_float(row.get("MBRevenue")),
                    "total_share": safe_float(row.get("totalShare")),
                    "float_share": safe_float(row.get("liqaShare")),
                    "quality_stat_date": row.get("statDate"),
                }
            )
        try:
            balance_df = to_frame(bs.query_balance_data(code=bs_code, year=year, quarter=quarter))
        except Exception:
            balance_df = None
        if balance_df is not None and not balance_df.empty:
            row = balance_df.iloc[0]
            quality.update(
                {
                    "debt_to_asset": pct_value(row.get("liabilityToAsset")),
                    "current_ratio": safe_float(row.get("currentRatio")),
                    "asset_to_equity": safe_float(row.get("assetToEquity")),
                }
            )
        try:
            cash_df = to_frame(bs.query_cash_flow_data(code=bs_code, year=year, quarter=quarter))
        except Exception:
            cash_df = None
        if cash_df is not None and not cash_df.empty:
            row = cash_df.iloc[0]
            quality.update(
                {
                    "cfo_to_np": safe_float(row.get("CFOToNP")),
                    "cfo_to_revenue": safe_float(row.get("CFOToOR")),
                }
            )
    return quality


def fetch_baostock_dividends(bs: Any, to_frame: Callable[[Any], Any], bs_code: str, end_date: str) -> dict[str, Any]:
    end_year = int(date_for_baostock(end_date)[:4])
    rows: list[dict[str, Any]] = []
    for year in range(end_year, end_year - 4, -1):
        try:
            df = to_frame(bs.query_dividend_data(code=bs_code, year=str(year), yearType="report"))
        except Exception:
            continue
        for _, row in df.iterrows():
            cash = safe_float(row.get("dividCashPsBeforeTax"), 0.0) or 0.0
            if cash > 0:
                rows.append({"year": year, "cash": cash})
    by_year: dict[int, float] = {}
    for row in rows:
        by_year[row["year"]] = by_year.get(row["year"], 0.0) + row["cash"]
    recent_years = sorted(by_year.keys(), reverse=True)[:3]
    recent_cash = by_year[recent_years[0]] if recent_years else 0.0
    continuity = sum(1 for year in recent_years if by_year.get(year, 0.0) > 0)
    return {
        "dividend_cash_per_share": recent_cash,
        "dividend_years_3y": continuity,
    }


def pct_value(value: Any) -> float | None:
    numeric = safe_float(value)
    if numeric is None:
        return None
    return numeric * 100


def fetch_shanghai_main_board_list(ak: Any) -> list[dict[str, Any]]:
    df = retry_call(
        ak.stock_info_sh_name_code,
        label="stock_info_sh_name_code",
        symbol="主板A股",
    )
    stocks: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        stocks.append(
            {
                "code": normalize_code(first_present(row, ["证券代码", "A_STOCK_CODE"])),
                "name": first_present(row, ["证券简称", "COMPANY_ABBR"], ""),
                "total_market_cap": None,
                "float_market_cap": None,
                "pe": None,
                "pb": None,
                "source": "sse_code_list",
            }
        )
    return stocks


def retry_call(func: Callable[..., Any], *, label: str, attempts: int = 3, delay_seconds: float = 1.5, **kwargs: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func(**kwargs)
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                print(f"[warn] {label} failed on attempt {attempt}/{attempts}: {exc}", file=sys.stderr)
                time.sleep(delay_seconds * attempt)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}") from last_error


def first_present(row: Any, fields: list[str], default: Any = None) -> Any:
    for field in fields:
        try:
            value = row.get(field)
        except AttributeError:
            value = None
        if value is not None and value != "":
            return value
    return default


def load_json_cache(path: Path, ttl_seconds: int) -> Any | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - payload.get("timestamp", 0) > ttl_seconds:
        return None
    return payload.get("data")


def save_json_cache(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": time.time(), "data": data}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def cached_fetch(path: Path, ttl_seconds: int, fetcher: Callable[[], Any], refresh: bool = False) -> Any:
    if not refresh:
        cached = load_json_cache(path, ttl_seconds)
        if cached is not None:
            return cached
    data = fetcher()
    save_json_cache(path, data)
    return data


def fetch_histories(
    stocks: Iterable[dict[str, Any]],
    fetch_history: Callable[[str, str, str], list[dict[str, Any]]],
    *,
    cache_dir: Path,
    start_date: str,
    end_date: str,
    cache_ttl_seconds: int,
    refresh: bool,
    limit: int | None,
) -> dict[str, list[dict[str, Any]]]:
    histories: dict[str, list[dict[str, Any]]] = {}
    stock_list = list(stocks)
    if limit:
        stock_list = stock_list[:limit]

    for index, stock in enumerate(stock_list, 1):
        code = normalize_code(stock.get("code"))
        print(f"[{index}/{len(stock_list)}] fetching {code} {stock.get('name', '')}", file=sys.stderr)
        cache_path = cache_dir / "history" / f"{code}_{start_date}_{end_date}.json"
        try:
            histories[code] = cached_fetch(
                cache_path,
                cache_ttl_seconds,
                lambda stock_code=code: fetch_history(stock_code, start_date, end_date),
                refresh=refresh,
            )
        except Exception as exc:
            print(f"[warn] history fetch failed for {code}: {exc}", file=sys.stderr)
            histories[code] = []
    return histories


def fetch_enrichments(
    stocks: Iterable[dict[str, Any]],
    fetch_enrichment: Callable[[str, str, str], dict[str, Any]] | None,
    *,
    cache_dir: Path,
    start_date: str,
    end_date: str,
    cache_ttl_seconds: int,
    refresh: bool,
) -> dict[str, dict[str, Any]]:
    if fetch_enrichment is None:
        return {}
    enrichments: dict[str, dict[str, Any]] = {}
    stock_list = list(stocks)
    for index, stock in enumerate(stock_list, 1):
        code = normalize_code(stock.get("code"))
        print(f"[{index}/{len(stock_list)}] enriching {code} {stock.get('name', '')}", file=sys.stderr)
        cache_path = cache_dir / "enrichment" / f"{code}_{end_date}.json"
        try:
            enrichments[code] = cached_fetch(
                cache_path,
                cache_ttl_seconds,
                lambda stock_code=code: fetch_enrichment(stock_code, start_date, end_date),
                refresh=refresh,
            )
        except Exception as exc:
            print(f"[warn] enrichment fetch failed for {code}: {exc}", file=sys.stderr)
            enrichments[code] = {}
    return enrichments


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rating",
        "score",
        "code",
        "name",
        "industry",
        "avg_amount_20d",
        "avg_amount_60d",
        "volatility_60d",
        "volatility_120d",
        "max_drawdown_120d",
        "return_5d",
        "return_20d",
        "return_60d",
        "return_120d",
        "total_market_cap",
        "float_market_cap",
        "market_cap_checked",
        "dividend_yield",
        "dividend_cash_per_share",
        "dividend_years_3y",
        "roe",
        "debt_to_asset",
        "cfo_to_np",
        "pe",
        "pb",
        "history_days",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_value(row.get(field)) for field in fields})


def format_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    return value


def write_markdown(
    path: Path,
    rows: list[dict[str, Any]],
    rejected: list[dict[str, str]],
    generated_at: str,
    provider_label: str = "AKShare",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 打新底仓候选筛选结果",
        "",
        f"- 生成时间：{generated_at}",
        f"- 数据源：{provider_label}",
        "- 用途：辅助筛选沪市主板打新市值底仓候选，不构成投资建议。",
        "- 风险提示：持仓股票可能下跌，亏损可能超过打新收益；请以券商口径和最新数据为准。",
        "- 若数据源缺少市值字段，市值门槛不会被硬性校验，候选行需额外复核市值。",
        "",
        "## 候选列表",
        "",
        "|评级|分数|代码|名称|行业|20日均成交额(亿元)|5日涨跌|20日涨跌|60日涨跌|120日涨跌|120日年化波动|120日最大回撤|总市值(亿元)|股息率(%)|ROE(%)|负债率(%)|PE|PB|",
        "|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "|{rating}|{score:.2f}|{code}|{name}|{industry}|{amount:.2f}|{ret_5d}|{ret_20d}|{ret_60d}|{ret_120d}|{vol:.2%}|{drawdown:.2%}|{cap}|{dividend:.2f}|{roe}|{debt}|{pe}|{pb}|".format(
                rating=row.get("rating", ""),
                score=safe_float(row.get("score"), 0.0) or 0.0,
                code=row.get("code", ""),
                name=row.get("name", ""),
                industry=row.get("industry", ""),
                amount=(safe_float(row.get("avg_amount_20d"), 0.0) or 0.0) / 1e8,
                ret_5d=markdown_percent(row.get("return_5d")),
                ret_20d=markdown_percent(row.get("return_20d")),
                ret_60d=markdown_percent(row.get("return_60d")),
                ret_120d=markdown_percent(row.get("return_120d")),
                vol=safe_float(row.get("volatility_120d"), 0.0) or 0.0,
                drawdown=safe_float(row.get("max_drawdown_120d"), 0.0) or 0.0,
                cap=markdown_yi(row.get("total_market_cap")),
                dividend=safe_float(row.get("dividend_yield"), 0.0) or 0.0,
                roe=markdown_number(row.get("roe")),
                debt=markdown_number(row.get("debt_to_asset")),
                pe=markdown_number(row.get("pe")),
                pb=markdown_number(row.get("pb")),
            )
        )
    lines.extend(
        [
            "",
            "## 剔除统计",
            "",
            f"- 剔除数量：{len(rejected)}",
            "- 常见原因：非沪市主板、ST/退市风险、历史数据不足、流动性不足、市值过小。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_baskets_markdown(
    path: Path,
    baskets: dict[str, list[dict[str, Any]]],
    final_picks: list[dict[str, Any]],
    generated_at: str,
    provider_label: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 打新底仓分篮子候选",
        "",
        f"- 生成时间：{generated_at}",
        f"- 数据源：{provider_label}",
        "- 用途：按不同底仓目标拆分候选池，不构成投资建议。",
        "- 篮子内已做行业数量限制，仍需结合账户额度、持仓比例和券商规则复核。",
        "- 篮子涨跌幅为篮子内候选股简单等权平均，周期按交易日计。",
        "",
        "## 篮子近期涨跌幅",
        "",
        "|篮子|成分数|5日|20日|60日|120日|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in build_basket_return_summary(baskets):
        lines.append(
            "|{basket}|{count}|{ret_5d}|{ret_20d}|{ret_60d}|{ret_120d}|".format(
                basket=row.get("basket", ""),
                count=row.get("count", 0),
                ret_5d=markdown_percent(row.get("return_5d")),
                ret_20d=markdown_percent(row.get("return_20d")),
                ret_60d=markdown_percent(row.get("return_60d")),
                ret_120d=markdown_percent(row.get("return_120d")),
            )
        )
    final_summary = {
        "basket": "最终杂篮子",
        "count": len(final_picks),
        **{f"return_{days}d": average_field(final_picks, f"return_{days}d") for days in RETURN_PERIODS},
    }
    lines.append(
        "|{basket}|{count}|{ret_5d}|{ret_20d}|{ret_60d}|{ret_120d}|".format(
            basket=final_summary["basket"],
            count=final_summary["count"],
            ret_5d=markdown_percent(final_summary.get("return_5d")),
            ret_20d=markdown_percent(final_summary.get("return_20d")),
            ret_60d=markdown_percent(final_summary.get("return_60d")),
            ret_120d=markdown_percent(final_summary.get("return_120d")),
        )
    )
    lines.append("")
    lines.extend(
        [
            "## 最终去重组合",
            "",
            "|序号|来源篮子|代码|名称|行业|篮子分|20日均成交额(亿元)|5日涨跌|20日涨跌|60日涨跌|120日涨跌|120日年化波动|120日最大回撤|总市值(亿元)|股息率(%)|ROE(%)|PE|PB|",
            "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in final_picks:
        lines.append(
            "|{order}|{basket}|{code}|{name}|{industry}|{basket_score:.2f}|{amount:.2f}|{ret_5d}|{ret_20d}|{ret_60d}|{ret_120d}|{vol:.2%}|{drawdown:.2%}|{cap}|{dividend:.2f}|{roe}|{pe}|{pb}|".format(
                order=row.get("final_pick_order", ""),
                basket=row.get("source_basket", ""),
                code=row.get("code", ""),
                name=row.get("name", ""),
                industry=row.get("industry", ""),
                basket_score=safe_float(row.get("basket_score"), 0.0) or 0.0,
                amount=(safe_float(row.get("avg_amount_20d"), 0.0) or 0.0) / 1e8,
                ret_5d=markdown_percent(row.get("return_5d")),
                ret_20d=markdown_percent(row.get("return_20d")),
                ret_60d=markdown_percent(row.get("return_60d")),
                ret_120d=markdown_percent(row.get("return_120d")),
                vol=safe_float(row.get("volatility_120d"), 0.0) or 0.0,
                drawdown=safe_float(row.get("max_drawdown_120d"), 0.0) or 0.0,
                cap=markdown_yi(row.get("total_market_cap")),
                dividend=safe_float(row.get("dividend_yield"), 0.0) or 0.0,
                roe=markdown_number(row.get("roe")),
                pe=markdown_number(row.get("pe")),
                pb=markdown_number(row.get("pb")),
            )
        )
    lines.append("")
    for basket_name, rows in baskets.items():
        lines.extend(
            [
                f"## {basket_name}",
                "",
                "|篮子分|代码|名称|行业|20日均成交额(亿元)|5日涨跌|20日涨跌|60日涨跌|120日涨跌|120日年化波动|120日最大回撤|总市值(亿元)|股息率(%)|ROE(%)|负债率(%)|PE|PB|",
                "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                "|{basket_score:.2f}|{code}|{name}|{industry}|{amount:.2f}|{ret_5d}|{ret_20d}|{ret_60d}|{ret_120d}|{vol:.2%}|{drawdown:.2%}|{cap}|{dividend:.2f}|{roe}|{debt}|{pe}|{pb}|".format(
                    basket_score=safe_float(row.get("basket_score"), 0.0) or 0.0,
                    code=row.get("code", ""),
                    name=row.get("name", ""),
                    industry=row.get("industry", ""),
                    amount=(safe_float(row.get("avg_amount_20d"), 0.0) or 0.0) / 1e8,
                    ret_5d=markdown_percent(row.get("return_5d")),
                    ret_20d=markdown_percent(row.get("return_20d")),
                    ret_60d=markdown_percent(row.get("return_60d")),
                    ret_120d=markdown_percent(row.get("return_120d")),
                    vol=safe_float(row.get("volatility_120d"), 0.0) or 0.0,
                    drawdown=safe_float(row.get("max_drawdown_120d"), 0.0) or 0.0,
                    cap=markdown_yi(row.get("total_market_cap")),
                    dividend=safe_float(row.get("dividend_yield"), 0.0) or 0.0,
                    roe=markdown_number(row.get("roe")),
                    debt=markdown_number(row.get("debt_to_asset")),
                    pe=markdown_number(row.get("pe")),
                    pb=markdown_number(row.get("pb")),
                )
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def markdown_number(value: Any) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return ""
    return f"{numeric:.2f}"


def markdown_percent(value: Any) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return ""
    return f"{numeric:.2%}"


def markdown_yi(value: Any) -> str:
    numeric = safe_float(value)
    if numeric is None or numeric <= 0:
        return ""
    return f"{numeric / 1e8:.2f}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="IPO base-holding stock screener")
    parser.add_argument(
        "--provider",
        choices=["baostock", "akshare"],
        default=DEFAULT_PROVIDER,
        help="primary data provider; baostock is the default for automatic screening",
    )
    parser.add_argument(
        "--universe",
        choices=["large_cap", "sz50", "hs300", "zz500", "all"],
        default=DEFAULT_UNIVERSE,
        help="automatic stock universe; large_cap means SSE 50 plus HS300 Shanghai main-board names",
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="number of rows to write")
    parser.add_argument("--basket-size", type=int, default=DEFAULT_BASKET_SIZE, help="rows per basket report")
    parser.add_argument("--final-per-basket", type=int, default=DEFAULT_FINAL_PER_BASKET, help="deduplicated final picks per basket")
    parser.add_argument("--min-history-days", type=int, default=DEFAULT_MIN_HISTORY_DAYS)
    parser.add_argument("--min-avg-amount", type=float, default=DEFAULT_MIN_AVG_AMOUNT)
    parser.add_argument("--min-market-cap", type=float, default=DEFAULT_MIN_MARKET_CAP)
    parser.add_argument("--lookback-days", type=int, default=420, help="calendar days of history to fetch")
    parser.add_argument("--cache-ttl-hours", type=float, default=24)
    parser.add_argument("--refresh", action="store_true", help="ignore local cache")
    parser.add_argument("--limit", type=int, help="debug limit before history fetch")
    parser.add_argument("--codes", help="comma-separated manual stock universe, e.g. 600000,601398,601988")
    parser.add_argument("--output-dir", type=Path, default=project_root / "outputs")
    parser.add_argument("--cache-dir", type=Path, default=project_root / ".cache" / "ipo_base_screener")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    provider = get_data_provider(args.provider)
    try:
        cache_ttl_seconds = int(args.cache_ttl_hours * 3600)

        end = datetime.now()
        start = end - timedelta(days=args.lookback_days)
        start_date = start.strftime("%Y%m%d")
        end_date = end.strftime("%Y%m%d")

        if args.codes:
            stocks = stocks_from_codes(args.codes)
        else:
            stock_cache_path = args.cache_dir / f"stock_list_{args.provider}_{args.universe}.json"
            stocks = cached_fetch(
                stock_cache_path,
                cache_ttl_seconds,
                lambda: provider.fetch_stock_list(args.universe),
                refresh=args.refresh,
            )
        prefiltered_stocks = [
            stock
            for stock in stocks
            if is_shanghai_main_board(stock.get("code"))
            and not is_st_name(stock.get("name"))
            and market_cap_passes_prefilter(stock.get("total_market_cap"), args.min_market_cap)
        ]
        histories = fetch_histories(
            prefiltered_stocks,
            provider.fetch_history,
            cache_dir=args.cache_dir,
            start_date=start_date,
            end_date=end_date,
            cache_ttl_seconds=cache_ttl_seconds,
            refresh=args.refresh,
            limit=args.limit,
        )
        apply_history_enrichment(stocks, histories)
        enrichment_stocks = prefiltered_stocks[: args.limit] if args.limit else prefiltered_stocks
        enrichments = fetch_enrichments(
            enrichment_stocks,
            provider.fetch_enrichment,
            cache_dir=args.cache_dir,
            start_date=start_date,
            end_date=end_date,
            cache_ttl_seconds=cache_ttl_seconds,
            refresh=args.refresh,
        )
        apply_fundamental_enrichment(stocks, histories, enrichments)

        result = screen_candidates(
            stocks,
            histories,
            min_history_days=args.min_history_days,
            min_avg_amount=args.min_avg_amount,
            min_market_cap=args.min_market_cap,
        )
        rows = result.rows[: args.top]

        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        csv_path = args.output_dir / "ipo_base_stock_candidates.csv"
        markdown_path = args.output_dir / "ipo_base_stock_candidates.md"
        baskets_path = args.output_dir / "ipo_base_stock_baskets.md"
        write_csv(csv_path, rows)
        write_markdown(markdown_path, rows, result.rejected, generated_at, provider.label)
        baskets = build_baskets(result.rows, per_basket=args.basket_size, max_per_industry=2)
        final_picks = build_final_picks(baskets, per_basket=args.final_per_basket)
        write_baskets_markdown(baskets_path, baskets, final_picks, generated_at, provider.label)

        print(f"Wrote {len(rows)} candidates to {csv_path}")
        print(f"Wrote markdown report to {markdown_path}")
        print(f"Wrote basket report to {baskets_path}")
        print(f"Rejected {len(result.rejected)} stocks")
        return 0
    finally:
        if provider.close:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
