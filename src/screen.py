from __future__ import annotations

import html
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import yaml
import yfinance as yf
from yfinance import EquityQuery

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.yml"
STATUS_PATH = DOCS / "latest.json"
LATEST_CSV = DOCS / "latest.csv"
HISTORY_PATH = DATA / "signal_history.csv"
UNIVERSE_CACHE_PATH = DATA / "universe_cache.csv"
LOG_PATH = DATA / "last_run.log"
SCHEMA_VERSION = "1.2"

DOCS.mkdir(parents=True, exist_ok=True)
ARCHIVE.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
LOGGER = logging.getLogger("screen")


@dataclass
class RunResult:
    status: str
    market_data_date: str | None = None
    message: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def get_latest_completed_market_date(config: dict[str, Any]) -> pd.Timestamp:
    """直近に完全終了したNYSE取引日を返す。"""

    now_utc = pd.Timestamp.now(tz="UTC")
    calendar = xcals.get_calendar("XNYS")

    start_date = (
        now_utc - pd.Timedelta(days=14)
    ).date().isoformat()

    end_date = (
        now_utc + pd.Timedelta(days=1)
    ).date().isoformat()

    schedule = calendar.schedule.loc[
        start_date:end_date
    ].copy()

    if schedule.empty:
        raise RuntimeError(
            "XNYSの取引日カレンダーを取得できませんでした"
        )

    buffer_minutes = int(
        config.get("market_close_buffer_minutes", 15)
    )

    completed_cutoff = (
        schedule["close"]
        + pd.Timedelta(minutes=buffer_minutes)
    )

    completed_schedule = schedule.loc[
        completed_cutoff <= now_utc
    ]

    if completed_schedule.empty:
        raise RuntimeError(
            "終了済みの米国市場取引日を特定できませんでした"
        )

    session = pd.Timestamp(
        completed_schedule.index[-1]
    )

    if session.tzinfo is not None:
        session = session.tz_localize(None)

    return session.normalize()


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def calculate_config_hash(config: dict[str, Any]) -> str:
    """Return a stable SHA-256 for the parsed, normalized configuration."""

    normalized = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_previous_status() -> dict[str, Any]:
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def json_safe(value: Any) -> Any:
    """Convert nested values to strict JSON, replacing non-finite numbers with null."""

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            json_safe(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def write_failure_status(reason: str, details: dict[str, Any] | None = None) -> None:
    previous = load_previous_status()
    payload: dict[str, Any] = {
        "status": "failed",
        "attempted_at": utc_now_iso(),
        "failure_reason": reason,
        "latest_csv_updated": False,
        "last_successful_market_date": (
            previous.get("market_data_date")
            if previous.get("status") == "success"
            else previous.get(
                "last_successful_market_date"
            )
        ),
        "last_successful_archive_file": (
            previous.get("archive_file")
            if previous.get("status") == "success"
            else previous.get(
                "last_successful_archive_file"
            )
        ),
        "csv_file": (
            "latest.csv"
            if LATEST_CSV.exists()
            else None
        ),
        "schema_version": SCHEMA_VERSION,
    }
    if details:
        payload.update(details)
    write_json(STATUS_PATH, payload)


def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace(".", "-")


def is_excluded_security(symbol: str, company_name: str) -> bool:
    # Preserve ordinary Class A/B shares (for example BRK-B), while removing
    # common Yahoo suffixes for warrants, units, rights, and preferred stock.
    if re.search(r"-(WT|WS|W|UN|U|R|P[A-Z]?)$", symbol):
        return True
    name = company_name.lower()
    excluded_phrases = (
        "warrant",
        "rights",
        "unit, each",
        "units, each",
        "acquisition corp",
        "acquisition corporation",
        "blank check",
    )
    return any(phrase in name for phrase in excluded_phrases)


def extract_number(record: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            value = value.get("raw")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def fetch_sector_map_from_yahoo(
    config: dict[str, Any],
) -> dict[str, str]:
    """セクター別スクリーナーからticker→sectorを作成する。"""

    sector_map: dict[str, str] = {}
    page_size = 250

    for sector_name in config["sector_etfs"]:
        LOGGER.info(
            "Fetching Yahoo sector universe: %s",
            sector_name,
        )

        query = EquityQuery(
            "and",
            [
                EquityQuery(
                    "is-in",
                    [
                        "exchange",
                        *config["exchanges"],
                    ],
                ),
                EquityQuery(
                    "eq",
                    [
                        "sector",
                        sector_name,
                    ],
                ),
                EquityQuery(
                    "gte",
                    [
                        "intradaymarketcap",
                        config["min_market_cap_usd"],
                    ],
                ),
                EquityQuery(
                    "gte",
                    [
                        "intradayprice",
                        config["min_price_usd"],
                    ],
                ),
            ],
        )

        offset = 0
        seen_in_sector: set[str] = set()

        while True:
            response = yf.screen(
                query,
                offset=offset,
                size=page_size,
                sortField="intradaymarketcap",
                sortAsc=False,
            )

            quotes = response.get("quotes", [])

            if not quotes:
                break

            new_count = 0

            for quote in quotes:
                ticker = normalize_symbol(
                    quote.get("symbol", "")
                )

                if (
                    not ticker
                    or ticker in seen_in_sector
                ):
                    continue

                seen_in_sector.add(ticker)
                sector_map[ticker] = sector_name
                new_count += 1

            total = int(
                response.get("total") or 0
            )

            offset += len(quotes)

            if (
                len(quotes) < page_size
                or new_count == 0
                or (total and offset >= total)
            ):
                break

            if offset > 20_000:
                raise RuntimeError(
                    "Yahoo sector screener pagination "
                    f"exceeded safety limit: {sector_name}"
                )

            time.sleep(0.5)

    if not sector_map:
        raise RuntimeError(
            "Yahoo sector screener returned no sector data"
        )

    return sector_map


def fetch_universe_from_yahoo(config: dict[str, Any]) -> pd.DataFrame:
    query = EquityQuery(
        "and",
        [
            EquityQuery("is-in", ["exchange", *config["exchanges"]]),
            EquityQuery("gte", ["intradaymarketcap", config["min_market_cap_usd"]]),
            EquityQuery("gte", ["intradayprice", config["min_price_usd"]]),
        ],
    )

    page_size = 250
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0

    while True:
        LOGGER.info("Fetching Yahoo screener universe offset=%s", offset)
        response = yf.screen(
            query,
            offset=offset,
            size=page_size,
            sortField="intradaymarketcap",
            sortAsc=False,
        )
        quotes = response.get("quotes", [])
        if not quotes:
            break

        new_count = 0
        for quote in quotes:
            symbol = normalize_symbol(quote.get("symbol", ""))
            if not symbol or symbol in seen:
                continue
            company_name = str(quote.get("shortName") or quote.get("longName") or symbol)
            if is_excluded_security(symbol, company_name):
                continue
            seen.add(symbol)
            new_count += 1
            records.append(
                {
                    "ticker": symbol,
                    "company_name": company_name,
                    "exchange": quote.get("exchange") or quote.get("fullExchangeName"),
                    "sector": quote.get("sector") or quote.get("sectorDisp"),
                    "industry": quote.get("industry") or quote.get("industryDisp"),
                    "market_cap": extract_number(
                        quote, "marketCap", "intradaymarketcap", "lastclosemarketcap.lasttwelvemonths"
                    ),
                    "screener_price": extract_number(quote, "regularMarketPrice", "intradayprice"),
                    "quote_type": quote.get("quoteType"),
                }
            )

        total = int(response.get("total") or 0)
        offset += len(quotes)
        if len(quotes) < page_size or new_count == 0 or (total and offset >= total):
            break
        if offset > 10000:
            raise RuntimeError("Yahoo screener pagination exceeded safety limit")
        time.sleep(1)

    universe = pd.DataFrame(records)
    if universe.empty:
        raise RuntimeError("Yahoo screener returned no eligible equities")

    universe = universe.drop_duplicates(
        "ticker"
    )

    universe = universe[
        universe["market_cap"].fillna(0)
        >= config["min_market_cap_usd"]
    ]

    universe = universe[
        universe["screener_price"].fillna(0)
        >= config["min_price_usd"]
    ]

    # 通常スクリーナーで欠損したセクターを、
    # セクター別スクリーナーの結果で補完する
    sector_map = fetch_sector_map_from_yahoo(
        config
    )

    existing_sector = (
        universe["sector"]
        .replace("", np.nan)
    )

    mapped_sector = (
        universe["ticker"]
        .map(sector_map)
    )

    universe["sector"] = (
        mapped_sector.combine_first(
            existing_sector
        )
    )

    universe["industry"] = (
        universe["industry"]
        .replace("", np.nan)
    )

    return universe.reset_index(drop=True)

def get_universe(
    config: dict[str, Any],
) -> tuple[pd.DataFrame, str]:
    try:
        universe = fetch_universe_from_yahoo(
            config
        )

        if len(universe) < config["min_universe_size"]:
            raise RuntimeError(
                f"Universe unexpectedly small: {len(universe)}"
            )

        sector_coverage = float(
            universe["sector"]
            .replace("", np.nan)
            .notna()
            .mean()
        )

        min_sector_coverage = float(
            config.get(
                "min_sector_coverage",
                0.90,
            )
        )

        LOGGER.info(
            "Live universe sector coverage: %.2f%%",
            sector_coverage * 100,
        )

        if sector_coverage < min_sector_coverage:
            raise RuntimeError(
                "ライブユニバースのセクター取得率が"
                "基準未満です。"
                f" coverage={sector_coverage:.2%},"
                f" required={min_sector_coverage:.2%}"
            )

        universe["universe_cached_at"] = (
            utc_now_iso()
        )

        universe.to_csv(
            UNIVERSE_CACHE_PATH,
            index=False,
        )

        return universe, "yahoo_screener"

    except Exception as exc:
        LOGGER.exception(
            "Live universe fetch failed"
        )

        if not UNIVERSE_CACHE_PATH.exists():
            raise RuntimeError(
                "Universe fetch failed and no cache exists: "
                f"{exc}"
            ) from exc

        universe = pd.read_csv(
            UNIVERSE_CACHE_PATH
        )

        if "sector" not in universe.columns:
            raise RuntimeError(
                "ユニバースキャッシュに"
                "sector列がありません"
            ) from exc

        cached_sector_coverage = float(
            universe["sector"]
            .replace("", np.nan)
            .notna()
            .mean()
        )

        min_sector_coverage = float(
            config.get(
                "min_sector_coverage",
                0.90,
            )
        )

        if (
            cached_sector_coverage
            < min_sector_coverage
        ):
            raise RuntimeError(
                "ユニバースキャッシュの"
                "セクター取得率が基準未満です。"
                f" coverage={cached_sector_coverage:.2%},"
                f" required={min_sector_coverage:.2%}"
            ) from exc

        cached_at = pd.to_datetime(
            universe.get(
                "universe_cached_at"
            ),
            utc=True,
            errors="coerce",
        ).max()

        if pd.isna(cached_at):
            cache_age_days = math.inf
        else:
            cache_age_days = (
                pd.Timestamp.now(tz="UTC")
                - cached_at
            ).total_seconds() / 86400

        if (
            cache_age_days
            > config["max_universe_cache_age_days"]
        ):
            raise RuntimeError(
                "Universe fetch failed and cache is "
                f"{cache_age_days:.1f} days old"
            ) from exc

        LOGGER.warning(
            "Using cached universe, age %.1f days",
            cache_age_days,
        )

        return universe, "cached_yahoo_screener"

def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def split_download_frame(frame: pd.DataFrame, requested: list[str]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    if frame is None or frame.empty:
        return result

    if isinstance(frame.columns, pd.MultiIndex):
        level0 = set(map(str, frame.columns.get_level_values(0)))
        level1 = set(map(str, frame.columns.get_level_values(1)))
        for ticker in requested:
            try:
                if ticker in level0:
                    sub = frame[ticker].copy()
                elif ticker in level1:
                    sub = frame.xs(ticker, axis=1, level=1).copy()
                else:
                    continue
                sub = sub.dropna(how="all")
                if not sub.empty:
                    result[ticker] = sub
            except (KeyError, ValueError):
                continue
    elif len(requested) == 1:
        sub = frame.dropna(how="all")
        if not sub.empty:
            result[requested[0]] = sub
    return result


def download_prices(tickers: list[str], config: dict[str, Any]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    downloaded: dict[str, pd.DataFrame] = {}
    batch_size = int(config["batch_size"])
    max_retries = int(config["max_retries"])

    for batch_number, batch in enumerate(chunks(tickers, batch_size), start=1):
        remaining = list(batch)
        for attempt in range(1, max_retries + 1):
            if not remaining:
                break
            LOGGER.info(
                "Downloading batch %s (%s tickers), attempt %s, remaining %s",
                batch_number,
                len(batch),
                attempt,
                len(remaining),
            )
            try:
                frame = yf.download(
                    tickers=remaining,
                    period=config["history_period"],
                    interval="1d",
                    auto_adjust=False,
                    actions=True,
                    repair=False,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    timeout=30,
                    multi_level_index=True,
                )
                parsed = split_download_frame(frame, remaining)
                downloaded.update(parsed)
                remaining = [ticker for ticker in remaining if ticker not in parsed]
            except Exception:
                LOGGER.exception("Download attempt failed")
            if remaining and attempt < max_retries:
                time.sleep(int(config["retry_wait_seconds"]) * attempt)

    failed = [ticker for ticker in tickers if ticker not in downloaded]
    return downloaded, failed


def download_required_benchmark(
    ticker: str,
    config: dict[str, Any],
) -> pd.DataFrame:
    """必須ベンチマークを取得し、失敗時は単独で再試行する。"""

    downloaded, _ = download_prices(
        [ticker],
        config,
    )

    if (
        ticker in downloaded
        and not downloaded[ticker].empty
    ):
        return downloaded[ticker]

    LOGGER.warning(
        "%sの通常取得に失敗しました。単独取得を再試行します。",
        ticker,
    )

    fallback = yf.download(
        tickers=ticker,
        period=config["history_period"],
        interval="1d",
        auto_adjust=False,
        actions=True,
        repair=False,
        threads=False,
        progress=False,
        timeout=int(
            config.get(
                "benchmark_fallback_timeout_seconds",
                60,
            )
        ),
        multi_level_index=False,
    )

    if fallback is None or fallback.empty:
        raise RuntimeError(
            f"Required benchmark {ticker} was not downloaded"
        )

    return fallback


def prepare_history(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Yahooの日次価格を計算用の統一列へ変換する。"""

    work = frame.copy()

    work.columns = [
        str(column)
        for column in work.columns
    ]

    work.index = (
        pd.to_datetime(
            work.index,
            errors="coerce",
        )
        .tz_localize(None)
    )

    work = (
        work[
            ~work.index.isna()
        ]
        .sort_index()
    )

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    if any(
        column not in work.columns
        for column in required
    ):
        return pd.DataFrame()

    work[required] = (
        work[required]
        .apply(
            pd.to_numeric,
            errors="coerce",
        )
    )

    work = work.dropna(
        subset=[
            "Open",
            "High",
            "Low",
            "Close",
        ]
    )

    work = work[
        (work["Open"] > 0)
        & (work["High"] > 0)
        & (work["Low"] > 0)
        & (work["Close"] > 0)
    ]

    if work.empty:
        return work

    # YahooのCloseは株式分割を反映した価格として使用する。
    # Adj Closeは配当の影響を含むため、価格リターンには使わない。
    # Adj_*という列名は後続処理との互換性維持のために使用する。
    work["Adj_Open"] = work["Open"]
    work["Adj_High"] = work["High"]
    work["Adj_Low"] = work["Low"]
    work["Adj_Close"] = work["Close"]
    work["Adj_Volume"] = work["Volume"]

    return work


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator is None or not np.isfinite(denominator) or denominator == 0:
        return np.nan
    return float(numerator / denominator)


def calculate_ticker_metrics(history: pd.DataFrame, market_date: pd.Timestamp) -> dict[str, Any] | None:
    data = prepare_history(history)

    if data.empty:
        return None

    # 市場時間中に取得された未確定の当日バーを除外する
    data = data[
        data.index.normalize()
        <= market_date.normalize()
    ]

    if (
        data.empty
        or len(data) < 30
        or data.index.max().normalize()
        != market_date.normalize()
    ):
        return None

    close = data["Adj_Close"]
    open_ = data["Adj_Open"]
    volume = data["Adj_Volume"]
    daily_return = close.pct_change()
    gap = open_ / close.shift(1) - 1

    latest_price = float(close.iloc[-1])
    ret_1d = float(close.iloc[-1] / close.iloc[-2] - 1) if len(close) >= 2 else np.nan
    ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else np.nan
    ret_21d = float(close.iloc[-1] / close.iloc[-22] - 1) if len(close) >= 22 else np.nan
    ret_63d = float(close.iloc[-1] / close.iloc[-64] - 1) if len(close) >= 64 else np.nan
    ret_126d = float(close.iloc[-1] / close.iloc[-127] - 1) if len(close) >= 127 else np.nan

    recent_volume = float(volume.iloc[-5:].mean()) if len(volume) >= 5 else np.nan
    previous_volume = float(volume.iloc[-25:-5].mean()) if len(volume) >= 25 else np.nan
    volume_ratio = safe_ratio(recent_volume, previous_volume)

    dollar_volume_20d = float((close * volume).iloc[-20:].mean()) if len(close) >= 20 else np.nan
    vol_20 = float(daily_return.iloc[-20:].std(ddof=1)) if len(daily_return) >= 21 else np.nan
    previous_120 = daily_return.iloc[-140:-20] if len(daily_return) >= 141 else daily_return.iloc[:-20]
    vol_120 = float(previous_120.std(ddof=1)) if len(previous_120.dropna()) >= 60 else np.nan
    volatility_ratio = safe_ratio(vol_20, vol_120)

    recent_daily_returns = daily_return.iloc[-21:].dropna()
    if recent_daily_returns.empty:
        max_daily_move_21d = np.nan
        max_daily_move_date_21d: str | None = None
        max_daily_move_signed_21d = np.nan
        max_1d_share_of_abs_move_21d = np.nan
        directional_efficiency_21d = np.nan
        post_max_move_return_5d = np.nan
        post_max_move_return_10d = np.nan
    else:
        max_move_timestamp = recent_daily_returns.abs().idxmax()
        max_daily_move_signed_21d = float(recent_daily_returns.loc[max_move_timestamp])
        max_daily_move_21d = abs(max_daily_move_signed_21d)
        max_daily_move_date_21d = max_move_timestamp.date().isoformat()
        total_abs_move_21d = float(recent_daily_returns.abs().sum())
        max_1d_share_of_abs_move_21d = safe_ratio(
            max_daily_move_21d,
            total_abs_move_21d,
        )
        log_returns = np.log1p(recent_daily_returns)
        directional_efficiency_21d = safe_ratio(
            abs(float(log_returns.sum())),
            float(log_returns.abs().sum()),
        )
        max_move_positions = np.flatnonzero(close.index == max_move_timestamp)
        max_move_position = int(max_move_positions[-1])

        def post_move_return(intervals: int) -> float:
            target_position = max_move_position + intervals
            if target_position >= len(close):
                return np.nan
            return float(
                close.iloc[target_position] / close.iloc[max_move_position] - 1
            )

        post_max_move_return_5d = post_move_return(5)
        post_max_move_return_10d = post_move_return(10)

    max_gap_21d = float(gap.iloc[-21:].abs().max())
    high_52w = float(close.iloc[-252:].max())
    low_52w = float(close.iloc[-252:].min())

    return {
        "market_data_date": market_date.date().isoformat(),
        "price": latest_price,
        "return_1d": ret_1d,
        "return_5d": ret_5d,
        "return_21d": ret_21d,
        "return_63d": ret_63d,
        "return_126d": ret_126d,
        "volume_ratio_5d_vs_prev20d": volume_ratio,
        "avg_dollar_volume_20d": dollar_volume_20d,
        "volatility_ratio_20d_vs_prev120d": volatility_ratio,
        "max_daily_move_21d": max_daily_move_21d,
        "max_daily_move_date_21d": max_daily_move_date_21d,
        "max_daily_move_signed_21d": max_daily_move_signed_21d,
        "max_1d_share_of_abs_move_21d": max_1d_share_of_abs_move_21d,
        "directional_efficiency_21d": directional_efficiency_21d,
        "post_max_move_return_5d": post_max_move_return_5d,
        "post_max_move_return_10d": post_max_move_return_10d,
        "max_gap_21d": max_gap_21d,
        "distance_from_52w_high": float(latest_price / high_52w - 1),
        "distance_from_52w_low": float(latest_price / low_52w - 1),
        "history_rows": int(len(data)),
    }


def validate_metric_dataframe(
    metrics: pd.DataFrame,
    config: dict[str, Any],
) -> list[str]:
    """
    価格系列の異常値を検査する。

    少数の孤立した異常銘柄は除外対象として返す。
    複数銘柄で異常が発生した場合は、
    系統的な価格調整不具合の可能性があるため停止する。
    """

    if metrics.empty:
        raise RuntimeError(
            "指標データが0行です"
        )

    threshold = float(
        config.get(
            "max_abs_daily_return_for_validation",
            2.0,
        )
    )

    suspicious = metrics[
        metrics["return_21d"]
        .abs()
        .gt(threshold)
        |
        metrics["max_daily_move_21d"]
        .abs()
        .gt(threshold)
        |
        metrics["max_gap_21d"]
        .abs()
        .gt(threshold)
    ].copy()

    if suspicious.empty:
        return []

    suspicious_tickers = (
        suspicious["ticker"]
        .astype(str)
        .drop_duplicates()
        .tolist()
    )

    sample = (
        suspicious[
            [
                "ticker",
                "return_21d",
                "max_daily_move_21d",
                "max_gap_21d",
            ]
        ]
        .head(20)
        .to_dict(orient="records")
    )

    allowed_count = int(
        config.get(
            "max_isolated_price_anomalies",
            1,
        )
    )

    if len(suspicious_tickers) > allowed_count:
        raise RuntimeError(
            "複数銘柄で価格異常値が検出されました。"
            "価格調整処理全体に問題がある可能性があります。"
            f" count={len(suspicious_tickers)},"
            f" sample={sample}"
        )

    LOGGER.warning(
        "孤立した価格異常値を持つ銘柄を除外します。"
        " tickers=%s sample=%s",
        suspicious_tickers,
        sample,
    )

    return suspicious_tickers

def threshold_hit(value: float, threshold: float, absolute: bool = False) -> bool:
    if value is None or not np.isfinite(value):
        return False
    return abs(value) >= threshold if absolute else value >= threshold


def build_triggers(row: pd.Series, config: dict[str, Any]) -> list[str]:
    triggers: list[str] = []
    rel_threshold = float(config["relative_return_threshold"])
    if threshold_hit(row.get("spy_relative_21d"), rel_threshold, absolute=True):
        triggers.append("spy_relative")
    if threshold_hit(row.get("sector_relative_21d"), rel_threshold, absolute=True):
        triggers.append("sector_relative")
    if threshold_hit(row.get("volume_ratio_5d_vs_prev20d"), float(config["volume_ratio_threshold"])):
        triggers.append("volume")
    if threshold_hit(
        row.get("volatility_ratio_20d_vs_prev120d"), float(config["volatility_ratio_threshold"])
    ):
        triggers.append("volatility")
    if threshold_hit(row.get("max_daily_move_21d"), float(config["single_day_move_threshold"])):
        triggers.append("single_day_move")
    if threshold_hit(row.get("max_gap_21d"), float(config["gap_threshold"])):
        triggers.append("gap")
    return triggers


def calculate_signal_score(row: pd.Series, config: dict[str, Any]) -> float:
    components = []
    pairs = [
        (abs(row.get("spy_relative_21d", np.nan)), config["relative_return_threshold"]),
        (abs(row.get("sector_relative_21d", np.nan)), config["relative_return_threshold"]),
        (row.get("volume_ratio_5d_vs_prev20d", np.nan), config["volume_ratio_threshold"]),
        (row.get("volatility_ratio_20d_vs_prev120d", np.nan), config["volatility_ratio_threshold"]),
        (row.get("max_daily_move_21d", np.nan), config["single_day_move_threshold"]),
        (row.get("max_gap_21d", np.nan), config["gap_threshold"]),
    ]
    for value, threshold in pairs:
        if value is not None and np.isfinite(value) and value >= threshold:
            components.append(float(value / threshold))
    return round(sum(components), 4)


def add_coverage_candidates(metrics: pd.DataFrame, config: dict[str, Any]) -> set[str]:
    selected: set[str] = set()
    usable = metrics.dropna(subset=["sector", "sector_relative_21d"])
    for _, group in usable.groupby("sector"):
        ordered = group.sort_values("sector_relative_21d")
        selected.update(ordered.head(int(config["sector_bottom_n"]))["ticker"])
        selected.update(ordered.tail(int(config["sector_top_n"]))["ticker"])
    return selected


def distribution_percentiles(values: pd.Series) -> dict[str, float | None]:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    if numeric.empty:
        return {"p10": None, "median": None, "p90": None}
    return {
        "p10": float(numeric.quantile(0.10)),
        "median": float(numeric.quantile(0.50)),
        "p90": float(numeric.quantile(0.90)),
    }


def calculate_universe_distributions(
    metrics: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Summarize the liquid pre-selection universe and each represented sector."""

    return_21d = pd.to_numeric(metrics["return_21d"], errors="coerce")
    spy_relative_21d = pd.to_numeric(metrics["spy_relative_21d"], errors="coerce")
    universe_distribution = {
        "return_21d": distribution_percentiles(return_21d),
        "spy_relative_21d": distribution_percentiles(spy_relative_21d),
        "sector_relative_21d": distribution_percentiles(
            metrics["sector_relative_21d"]
        ),
        "return_63d": distribution_percentiles(metrics["return_63d"]),
        "return_21d_gt_20pct_count": int(return_21d.gt(0.20).sum()),
        "return_21d_lt_minus_20pct_count": int(return_21d.lt(-0.20).sum()),
        "abs_spy_relative_21d_gt_8pct_count": int(
            spy_relative_21d.abs().gt(0.08).sum()
        ),
    }

    sector_distribution: dict[str, Any] = {}
    sector_metrics = metrics.copy()
    sector_metrics["sector"] = sector_metrics["sector"].replace("", np.nan)
    for sector, group in sector_metrics.dropna(subset=["sector"]).groupby(
        "sector",
        sort=True,
    ):
        sector_distribution[str(sector)] = {
            "count": int(len(group)),
            "sector_relative_21d": distribution_percentiles(
                group["sector_relative_21d"]
            ),
            "return_63d": {
                "median": distribution_percentiles(group["return_63d"])[
                    "median"
                ]
            },
        }

    return universe_distribution, sector_distribution


def load_signal_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        return pd.DataFrame(columns=["market_data_date", "ticker", "rank", "signal_score", "trigger_count"])
    history = pd.read_csv(HISTORY_PATH)
    if not history.empty:
        history["market_data_date"] = pd.to_datetime(history["market_data_date"]).dt.date.astype(str)
    return history


def add_persistence_fields(candidates: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    current_date = str(candidates["market_data_date"].iloc[0])
    prior_dates = sorted(history["market_data_date"].dropna().unique().tolist())
    all_dates = prior_dates + ([current_date] if current_date not in prior_dates else [])
    last_5 = set(all_dates[-5:])
    last_10 = set(all_dates[-10:])
    previous_date = prior_dates[-1] if prior_dates else None

    previous_rank = {}
    if previous_date:
        previous_rows = history[history["market_data_date"] == previous_date]
        previous_rank = dict(zip(previous_rows["ticker"], previous_rows["rank"]))

    history_by_ticker = {
        ticker: set(group["market_data_date"].astype(str)) for ticker, group in history.groupby("ticker")
    }

    first_dates = {}
    for ticker, dates in history_by_ticker.items():
        first_dates[ticker] = min(dates) if dates else current_date

    rows = []
    for _, row in candidates.iterrows():
        ticker = row["ticker"]
        prior_trigger_dates = history_by_ticker.get(ticker, set())
        dates_with_current = prior_trigger_dates | {current_date}
        consecutive = 0
        for date_value in reversed(all_dates):
            if date_value in dates_with_current:
                consecutive += 1
            else:
                break
        row = row.copy()
        row["first_trigger_date"] = min(first_dates.get(ticker, current_date), current_date)
        row["trigger_days_last_5"] = len(dates_with_current & last_5)
        row["trigger_days_last_10"] = len(dates_with_current & last_10)
        row["consecutive_trigger_days"] = consecutive
        row["rank_previous"] = previous_rank.get(ticker, np.nan)
        row["rank_change"] = (
            float(previous_rank[ticker] - row["rank"]) if ticker in previous_rank else np.nan
        )
        row["new_entry"] = ticker not in prior_trigger_dates
        row["persistent_signal"] = bool(
            row["trigger_days_last_5"] >= 4 or row["consecutive_trigger_days"] >= 3
        )
        rows.append(row)
    return pd.DataFrame(rows)


def append_history(candidates: pd.DataFrame, history: pd.DataFrame) -> None:
    new_rows = candidates[["market_data_date", "ticker", "rank", "signal_score", "trigger_count"]].copy()
    combined = pd.concat([history, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(["market_data_date", "ticker"], keep="last")
    combined = combined.sort_values(["market_data_date", "rank"])
    combined.to_csv(HISTORY_PATH, index=False)


REQUIRED_OUTPUT_COLUMNS = [
    "rank",
    "ticker",
    "company_name",
    "sector",
    "market_cap",
    "market_data_date",
    "price",
    "return_21d",
    "return_63d",
    "return_126d",
    "spy_return_63d",
    "spy_return_126d",
    "spy_relative_21d",
    "spy_relative_63d",
    "spy_relative_126d",
    "sector_etf",
    "sector_etf_return_21d",
    "sector_etf_return_63d",
    "sector_etf_return_126d",
    "sector_relative_21d",
    "sector_relative_63d",
    "sector_relative_126d",
    "volume_ratio_5d_vs_prev20d",
    "avg_dollar_volume_20d",
    "volatility_ratio_20d_vs_prev120d",
    "max_daily_move_21d",
    "max_daily_move_date_21d",
    "max_daily_move_signed_21d",
    "max_1d_share_of_abs_move_21d",
    "directional_efficiency_21d",
    "post_max_move_return_5d",
    "post_max_move_return_10d",
    "max_gap_21d",
    "distance_from_52w_high",
    "distance_from_52w_low",
    "trigger_conditions",
    "selection_reason",
    "signal_score",
]


def validate_output_dataframe(
    candidates: pd.DataFrame,
    market_date: pd.Timestamp,
    config: dict[str, Any],
) -> None:
    """保存前の候補CSVを検証する。"""

    missing_columns = [
        column
        for column in REQUIRED_OUTPUT_COLUMNS
        if column not in candidates.columns
    ]

    if missing_columns:
        raise RuntimeError(
            "出力CSVの必須列が不足しています。"
            f" missing={missing_columns}"
        )

    if candidates.empty:
        raise RuntimeError(
            "候補CSVが0行です"
        )

    if candidates["ticker"].isna().any():
        raise RuntimeError(
            "tickerが空欄の行があります"
        )

    duplicated = candidates.loc[
        candidates["ticker"].duplicated(
            keep=False
        ),
        "ticker",
    ].tolist()

    if duplicated:
        raise RuntimeError(
            "tickerが重複しています。"
            f" tickers={duplicated[:20]}"
        )

    output_dates = pd.to_datetime(
        candidates["market_data_date"],
        errors="coerce",
    ).dt.normalize()

    expected_date = market_date.normalize()

    if (
        output_dates.isna().any()
        or not output_dates.eq(
            expected_date
        ).all()
    ):
        raise RuntimeError(
            "CSV内のmarket_data_dateが"
            "基準市場日と一致しません"
        )

    sector_coverage = float(
        candidates["sector"]
        .replace("", np.nan)
        .notna()
        .mean()
    )

    required_coverage = float(
        config.get(
            "min_sector_coverage",
            0.90,
        )
    )

    if sector_coverage < required_coverage:
        raise RuntimeError(
            "候補CSVのセクター取得率が基準未満です。"
            f" coverage={sector_coverage:.2%},"
            f" required={required_coverage:.2%}"
        )


def format_value(value: Any, kind: str = "number") -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if kind == "percent":
        return f"{float(value) * 100:.1f}%"
    if kind == "ratio":
        return f"{float(value):.2f}x"
    if kind == "usd":
        number = float(value)
        if abs(number) >= 1e9:
            return f"${number / 1e9:.1f}B"
        if abs(number) >= 1e6:
            return f"${number / 1e6:.1f}M"
        return f"${number:,.0f}"
    return html.escape(str(value))


def generate_html(candidates: pd.DataFrame, status: dict[str, Any]) -> None:
    top = candidates.head(50)
    rows = []
    for _, row in top.iterrows():
        rows.append(
            "<tr>"
            f"<td>{int(row['rank'])}</td>"
            f"<td><strong>{html.escape(str(row['ticker']))}</strong></td>"
            f"<td>{html.escape(str(row['company_name']))}</td>"
            f"<td>{html.escape(str(row.get('sector') or '—'))}</td>"
            f"<td>{format_value(row['return_21d'], 'percent')}</td>"
            f"<td>{format_value(row['spy_relative_21d'], 'percent')}</td>"
            f"<td>{format_value(row['sector_relative_21d'], 'percent')}</td>"
            f"<td>{format_value(row['volume_ratio_5d_vs_prev20d'], 'ratio')}</td>"
            f"<td>{html.escape(str(row['trigger_conditions']))}</td>"
            f"<td>{'Yes' if row['persistent_signal'] else 'No'}</td>"
            "</tr>"
        )

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    raw_base = f"https://raw.githubusercontent.com/{repository}/main/docs" if repository else ""
    raw_links = (
        f'<a href="{raw_base}/latest.csv">Raw CSV</a> · '
        f'<a href="{raw_base}/latest.json">Raw JSON</a>'
        if raw_base
        else '<a href="latest.csv">CSV</a> · <a href="latest.json">JSON</a>'
    )

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>US stock screening</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:1500px;margin:32px auto;padding:0 16px;color:#1f2328}}
h1{{margin-bottom:4px}} .meta{{color:#59636e;margin-bottom:18px}} .links{{margin:16px 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border-bottom:1px solid #d8dee4;padding:8px;text-align:right;white-space:nowrap}}
th{{position:sticky;top:0;background:#f6f8fa}} td:nth-child(2),td:nth-child(3),td:nth-child(4),td:nth-child(9),th:nth-child(2),th:nth-child(3),th:nth-child(4),th:nth-child(9){{text-align:left}}
.wrapper{{overflow-x:auto}} code{{background:#f6f8fa;padding:2px 5px;border-radius:4px}}
</style>
</head>
<body>
<h1>US stock screening</h1>
<div class="meta">Market date: <strong>{html.escape(str(status['market_data_date']))}</strong> · Generated: {html.escape(str(status['generated_at']))} · Candidates: {status['row_count']} · Coverage: {status['data_coverage']:.1%}</div>
<div class="links">{raw_links} · <a href="latest.csv">Download CSV</a> · <a href="latest.json">Status JSON</a></div>
<p>Price returns are split-adjusted and exclude dividends. Sector coverage candidates are included even when no threshold is crossed.</p>
<div class="wrapper"><table>
<thead><tr><th>Rank</th><th>Ticker</th><th>Company</th><th>Sector</th><th>21d return</th><th>vs SPY</th><th>vs sector</th><th>Volume ratio</th><th>Triggers</th><th>Persistent</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table></div>
</body></html>"""
    (DOCS / "index.html").write_text(page, encoding="utf-8")


def run() -> RunResult:
    config = load_config()
    config_hash = calculate_config_hash(config)
    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"
    market_symbol = config["market_benchmark"]

    # 必須ベンチマークを取得する
    market_frame = download_required_benchmark(
        market_symbol,
        config,
    )

    market_history = prepare_history(
        market_frame
    )

    if market_history.empty:
        raise RuntimeError(
            f"Required benchmark {market_symbol} has no usable history"
        )

    # 取引所カレンダーから直近の終了済み市場日を取得する
    expected_market_date = (
        get_latest_completed_market_date(config)
    )

    # 未終了の当日バーや未来日データを除外する
    market_history = market_history[
        market_history.index.normalize()
        <= expected_market_date
    ]

    if market_history.empty:
        raise RuntimeError(
            "SPYに終了済み市場日のデータがありません。"
            f" expected={expected_market_date.date()}"
        )

    downloaded_market_date = (
        market_history.index.max().normalize()
    )

    if downloaded_market_date != expected_market_date:
        raise RuntimeError(
            "SPYの最終市場日が、直近の終了済み取引日と"
            "一致しません。"
            f" expected={expected_market_date.date()},"
            f" downloaded={downloaded_market_date.date()}"
        )

    market_date = expected_market_date
    market_date_str = (
        market_date.date().isoformat()
    )

    # 後続処理との互換性を維持する
    quick_prices = {
        market_symbol: market_frame,
    }
    quick_failed: list[str] = []

    previous_status = load_previous_status()
    if (
        not force_run
        and previous_status.get("status") == "success"
        and previous_status.get("market_data_date") == market_date_str
    ):
        LOGGER.info("No new market date; keeping existing outputs")
        return RunResult(status="no_update", market_data_date=market_date_str)

    universe, universe_source = get_universe(
        config
    )

    LOGGER.info(
        "Universe size: %s (%s)",
        len(universe),
        universe_source,
    )

    sector_data_coverage = float(
        universe["sector"]
        .replace("", np.nan)
        .notna()
        .mean()
    )

    industry_data_coverage = float(
        universe["industry"]
        .replace("", np.nan)
        .notna()
        .mean()
    )

    benchmarks = [market_symbol, *config["sector_etfs"].values()]
    remaining_tickers = list(
        dict.fromkeys(universe["ticker"].astype(str).tolist() + benchmarks)
    )
    remaining_tickers = [ticker for ticker in remaining_tickers if ticker != market_symbol]
    price_data, failed = download_prices(remaining_tickers, config)
    price_data[market_symbol] = quick_prices[market_symbol]
    failed = [ticker for ticker in failed if ticker != market_symbol] + quick_failed

    benchmark_metrics: dict[str, dict[str, Any]] = {}
    missing_benchmarks = []
    for symbol in benchmarks:
        metrics = calculate_ticker_metrics(price_data.get(symbol, pd.DataFrame()), market_date)
        if metrics is None:
            missing_benchmarks.append(symbol)
        else:
            benchmark_metrics[symbol] = metrics
    if missing_benchmarks:
        raise RuntimeError(f"Missing or stale benchmarks: {', '.join(missing_benchmarks)}")

    metric_rows = []
    universe_lookup = universe.set_index("ticker").to_dict("index")
    for ticker in universe["ticker"].astype(str):
        metrics = calculate_ticker_metrics(price_data.get(ticker, pd.DataFrame()), market_date)
        if metrics is None:
            continue
        metadata = universe_lookup[ticker]
        metric_rows.append({"ticker": ticker, **metadata, **metrics})

    metrics_df = pd.DataFrame(
        metric_rows
    )

    quality_excluded_tickers = (
        validate_metric_dataframe(
            metrics_df,
            config,
        )
    )

    if quality_excluded_tickers:
        metrics_df = metrics_df[
            ~metrics_df["ticker"].isin(
                quality_excluded_tickers
            )
        ].copy()

        LOGGER.warning(
            "品質検査により%d銘柄を"
            "定量候補母集団から除外しました: %s",
            len(quality_excluded_tickers),
            quality_excluded_tickers,
        )

    if metrics_df.empty:
        raise RuntimeError(
            "品質検査後の指標データが0行です"
        )

    coverage = (
        len(metrics_df) / len(universe)
        if len(universe)
        else 0
    )
    
    if coverage < float(config["min_data_coverage"]):
        raise RuntimeError(
            f"Insufficient data coverage: {coverage:.1%} ({len(metrics_df)}/{len(universe)})"
        )

    metrics_df = metrics_df[
        (metrics_df["price"] >= float(config["min_price_usd"]))
        & (metrics_df["avg_dollar_volume_20d"] >= float(config["min_avg_dollar_volume_20d"]))
    ].copy()
    for horizon in ("21d", "63d", "126d"):
        spy_return = benchmark_metrics[market_symbol][f"return_{horizon}"]
        metrics_df[f"spy_return_{horizon}"] = spy_return
        metrics_df[f"spy_relative_{horizon}"] = (
            metrics_df[f"return_{horizon}"] - spy_return
        )

    def sector_return(sector: Any, horizon: str) -> float:
        etf = config["sector_etfs"].get(sector)
        return benchmark_metrics.get(etf, {}).get(f"return_{horizon}", np.nan)

    metrics_df["sector_etf"] = metrics_df["sector"].map(config["sector_etfs"])
    for horizon in ("21d", "63d", "126d"):
        metrics_df[f"sector_etf_return_{horizon}"] = metrics_df["sector"].map(
            lambda sector, period=horizon: sector_return(sector, period)
        )
        metrics_df[f"sector_relative_{horizon}"] = (
            metrics_df[f"return_{horizon}"]
            - metrics_df[f"sector_etf_return_{horizon}"]
        )

    universe_distribution, sector_distribution = calculate_universe_distributions(
        metrics_df
    )
    metrics_df["trigger_list"] = metrics_df.apply(lambda row: build_triggers(row, config), axis=1)
    metrics_df["trigger_count"] = metrics_df["trigger_list"].map(len)
    metrics_df["signal_score"] = metrics_df.apply(lambda row: calculate_signal_score(row, config), axis=1)

    coverage_tickers = add_coverage_candidates(metrics_df, config)
    metrics_df["coverage_candidate"] = metrics_df["ticker"].isin(coverage_tickers)
    candidates = metrics_df[(metrics_df["trigger_count"] > 0) | metrics_df["coverage_candidate"]].copy()
    if candidates.empty:
        raise RuntimeError("No candidates were produced")

    candidates["trigger_conditions"] = candidates["trigger_list"].apply(
        lambda values: ";".join(values) if values else "sector_coverage"
    )
    candidates["selection_reason"] = np.where(
        (candidates["trigger_count"] > 0) & candidates["coverage_candidate"],
        "threshold_and_sector_coverage",
        np.where(candidates["trigger_count"] > 0, "threshold", "sector_coverage"),
    )
    candidates = candidates.sort_values(
        ["signal_score", "trigger_count", "avg_dollar_volume_20d"], ascending=[False, False, False]
    ).head(int(config["max_candidates"]))
    candidates.insert(0, "rank", range(1, len(candidates) + 1))

    history = load_signal_history()
    candidates = add_persistence_fields(candidates, history)

    output_columns = [
        "rank",
        "ticker",
        "company_name",
        "sector",
        "industry",
        "exchange",
        "market_cap",
        "market_data_date",
        "price",
        "return_1d",
        "return_5d",
        "return_21d",
        "return_63d",
        "return_126d",
        "spy_return_63d",
        "spy_return_126d",
        "spy_relative_21d",
        "spy_relative_63d",
        "spy_relative_126d",
        "sector_etf",
        "sector_etf_return_21d",
        "sector_etf_return_63d",
        "sector_etf_return_126d",
        "sector_relative_21d",
        "sector_relative_63d",
        "sector_relative_126d",
        "volume_ratio_5d_vs_prev20d",
        "avg_dollar_volume_20d",
        "volatility_ratio_20d_vs_prev120d",
        "max_daily_move_21d",
        "max_daily_move_date_21d",
        "max_daily_move_signed_21d",
        "max_1d_share_of_abs_move_21d",
        "directional_efficiency_21d",
        "post_max_move_return_5d",
        "post_max_move_return_10d",
        "max_gap_21d",
        "distance_from_52w_high",
        "distance_from_52w_low",
        "trigger_conditions",
        "trigger_count",
        "selection_reason",
        "signal_score",
        "first_trigger_date",
        "trigger_days_last_5",
        "trigger_days_last_10",
        "consecutive_trigger_days",
        "rank_previous",
        "rank_change",
        "new_entry",
        "persistent_signal",
    ]
    candidates = candidates[output_columns]

    validate_output_dataframe(
        candidates=candidates,
        market_date=market_date,
        config=config,
    )

    temp_csv = DOCS / "latest.tmp.csv"
    candidates.to_csv(temp_csv, index=False, float_format="%.8f")
    archive_path = ARCHIVE / f"screening_{market_date_str}.csv"
    candidates.to_csv(archive_path, index=False, float_format="%.8f")
    temp_csv.replace(LATEST_CSV)
    append_history(candidates, history)

    status = {
        "status": "success",
        "generated_at": utc_now_iso(),
        "market_data_date": market_date_str,
        "expected_market_data_date": (
            expected_market_date.date().isoformat()
        ),
        "is_market_data_complete": True,
        "last_successful_market_date": (
            market_date_str
        ),
        "row_count": int(len(candidates)),
        "universe_count": int(len(universe)),
        "usable_ticker_count": int(len(metric_rows)),
        "liquid_ticker_count": int(len(metrics_df)),
        "failed_ticker_count": int(len(failed)),
        "failed_tickers_sample": failed[:50],
        "quality_excluded_count": int(
            len(quality_excluded_tickers)
        ),
        "quality_excluded_tickers": (
            quality_excluded_tickers
        ),
        "universe_distribution": universe_distribution,
        "sector_distribution": sector_distribution,
        "data_coverage": round(float(coverage), 6),
        "sector_data_coverage": round(
            sector_data_coverage,
            6,
        ),
        "industry_data_coverage": round(
            industry_data_coverage,
            6,
        ),
        "universe_source": universe_source,
        "csv_file": "latest.csv",
        "archive_file": f"archive/{archive_path.name}",
        "last_successful_archive_file": (
            f"archive/{archive_path.name}"
        ),
        "latest_csv_updated": True,
        "required_column_check": "success",
        "numeric_validation_status": "success",
        "price_adjustment_validation_status": (
            "success"
        ),
        "schema_version": SCHEMA_VERSION,
        "config_version": config.get("config_version"),
        "config_hash": config_hash,
        "price_return_definition": "split-adjusted price return excluding dividends",
        "return_21d_definition": "close(t) / close(t-21 trading intervals) - 1",
        "volume_ratio_definition": "mean adjusted volume, latest 5 sessions / preceding 20 sessions",
        "repository": os.environ.get("GITHUB_REPOSITORY"),
    }
    write_json(STATUS_PATH, status)
    generate_html(candidates, status)
    LOGGER.info("Created %s candidates for %s", len(candidates), market_date_str)
    return RunResult(status="success", market_data_date=market_date_str)


def main() -> None:
    try:
        result = run()
        LOGGER.info("Run result: %s", result)
    except Exception as exc:
        LOGGER.exception("Screening failed")
        write_failure_status(str(exc))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
