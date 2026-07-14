from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.screen import (
    QUIET_DRIFT_OUTPUT_COLUMNS,
    build_quiet_drift_candidates,
    calculate_relative_price_metrics,
    calculate_ticker_metrics,
    calculate_universe_distributions,
    sector_coverage_strength_allowed,
    validate_metric_dataframe,
    validate_quiet_drift_dataframe,
    write_dataframe_csv,
)


def price_history(
    daily_returns: np.ndarray,
    *,
    end: str = "2026-07-13",
) -> pd.DataFrame:
    closes = np.concatenate(
        ([100.0], 100.0 * np.cumprod(1.0 + daily_returns))
    )
    index = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.full(len(closes), 1_000_000.0),
        },
        index=index,
    )


class TickerMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.daily_returns = np.full(149, 0.001)
        self.max_move_position = 135
        self.daily_returns[self.max_move_position - 1] = -0.15
        self.history = price_history(self.daily_returns)
        self.market_date = self.history.index[-1]
        self.metrics = calculate_ticker_metrics(self.history, self.market_date)
        assert self.metrics is not None

    def test_long_returns(self) -> None:
        close = self.history["Close"]
        self.assertAlmostEqual(
            self.metrics["return_63d"],
            close.iloc[-1] / close.iloc[-64] - 1,
        )
        self.assertAlmostEqual(
            self.metrics["return_126d"],
            close.iloc[-1] / close.iloc[-127] - 1,
        )

    def test_max_move_date_and_sign(self) -> None:
        expected_date = self.history.index[self.max_move_position].date().isoformat()
        self.assertEqual(self.metrics["max_daily_move_date_21d"], expected_date)
        self.assertAlmostEqual(self.metrics["max_daily_move_signed_21d"], -0.15)
        self.assertAlmostEqual(self.metrics["max_daily_move_21d"], 0.15)

    def test_post_max_move_returns(self) -> None:
        close = self.history["Close"]
        move = self.max_move_position
        self.assertAlmostEqual(
            self.metrics["post_max_move_return_5d"],
            close.iloc[move + 5] / close.iloc[move] - 1,
        )
        self.assertAlmostEqual(
            self.metrics["post_max_move_return_10d"],
            close.iloc[move + 10] / close.iloc[move] - 1,
        )

    def test_shock_concentration_and_directional_efficiency(self) -> None:
        total_abs_move = 0.15 + 20 * 0.001
        recent_returns = self.history["Close"].pct_change().iloc[-21:]
        log_returns = np.log1p(recent_returns)
        self.assertAlmostEqual(
            self.metrics["max_1d_share_of_abs_move_21d"],
            0.15 / total_abs_move,
        )
        self.assertAlmostEqual(
            self.metrics["directional_efficiency_21d"],
            abs(float(log_returns.sum())) / float(log_returns.abs().sum()),
        )
        self.assertGreaterEqual(self.metrics["directional_efficiency_21d"], 0.0)
        self.assertLessEqual(self.metrics["directional_efficiency_21d"], 1.0)

    def test_directional_efficiency_is_one_for_same_direction_returns(self) -> None:
        history = price_history(np.full(60, 0.01))
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        self.assertAlmostEqual(metrics["directional_efficiency_21d"], 1.0)

    def test_directional_efficiency_is_near_zero_for_offsetting_returns(self) -> None:
        up = 0.01
        down = 1.0 / (1.0 + up) - 1.0
        recent_returns = np.array([up, down] * 10 + [0.0])
        daily_returns = np.concatenate((np.full(39, 0.002), recent_returns))
        history = price_history(daily_returns)
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        self.assertAlmostEqual(metrics["directional_efficiency_21d"], 0.0, places=12)

    def test_missing_followup_and_long_history_are_nan(self) -> None:
        daily_returns = np.full(79, 0.001)
        daily_returns[76] = 0.20
        history = price_history(daily_returns)
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        self.assertTrue(math.isnan(metrics["post_max_move_return_5d"]))
        self.assertTrue(math.isnan(metrics["post_max_move_return_10d"]))
        self.assertTrue(math.isnan(metrics["return_126d"]))

    def test_flat_history_handles_zero_denominators(self) -> None:
        history = price_history(np.zeros(129))
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        self.assertTrue(math.isnan(metrics["max_1d_share_of_abs_move_21d"]))
        self.assertTrue(math.isnan(metrics["directional_efficiency_21d"]))
        self.assertTrue(math.isnan(metrics["max_1d_share_of_abs_move_63d"]))
        self.assertTrue(math.isnan(metrics["directional_efficiency_63d"]))
        self.assertEqual(metrics["positive_days_63d"], 0)
        self.assertEqual(metrics["negative_days_63d"], 0)
        self.assertTrue(math.isnan(metrics["positive_day_ratio_63d"]))

    def test_63d_metrics(self) -> None:
        recent_returns = self.history["Close"].pct_change().iloc[-63:]
        recent_gaps = self.history["Open"] / self.history["Close"].shift(1) - 1
        log_returns = np.log1p(recent_returns)
        self.assertAlmostEqual(self.metrics["max_daily_move_63d"], 0.15)
        self.assertAlmostEqual(
            self.metrics["max_gap_63d"],
            float(recent_gaps.iloc[-63:].abs().max()),
        )
        self.assertAlmostEqual(
            self.metrics["max_1d_share_of_abs_move_63d"],
            0.15 / float(recent_returns.abs().sum()),
        )
        self.assertAlmostEqual(
            self.metrics["directional_efficiency_63d"],
            abs(float(log_returns.sum())) / float(log_returns.abs().sum()),
        )
        self.assertEqual(self.metrics["positive_days_63d"], 62)
        self.assertEqual(self.metrics["negative_days_63d"], 1)
        self.assertAlmostEqual(self.metrics["positive_day_ratio_63d"], 62 / 63)
        self.assertGreaterEqual(self.metrics["directional_efficiency_63d"], 0.0)
        self.assertLessEqual(self.metrics["directional_efficiency_63d"], 1.0)

    def test_126d_metrics(self) -> None:
        recent_returns = self.history["Close"].pct_change().iloc[-126:]
        recent_gaps = self.history["Open"] / self.history["Close"].shift(1) - 1
        log_returns = np.log1p(recent_returns)
        self.assertAlmostEqual(
            self.metrics["max_daily_move_126d"],
            float(recent_returns.abs().max()),
        )
        self.assertAlmostEqual(
            self.metrics["max_gap_126d"],
            float(recent_gaps.iloc[-126:].abs().max()),
        )
        self.assertAlmostEqual(
            self.metrics["max_1d_share_of_abs_move_126d"],
            float(recent_returns.abs().max()) / float(recent_returns.abs().sum()),
        )
        self.assertAlmostEqual(
            self.metrics["directional_efficiency_126d"],
            abs(float(log_returns.sum())) / float(log_returns.abs().sum()),
        )
        self.assertEqual(self.metrics["positive_days_126d"], 125)
        self.assertEqual(self.metrics["negative_days_126d"], 1)
        self.assertAlmostEqual(self.metrics["positive_day_ratio_126d"], 125 / 126)

    def test_63d_directional_efficiency_same_direction(self) -> None:
        history = price_history(np.full(90, 0.01))
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        self.assertAlmostEqual(metrics["directional_efficiency_63d"], 1.0)

    def test_63d_directional_efficiency_offsetting(self) -> None:
        up = 0.01
        down = 1.0 / (1.0 + up) - 1.0
        recent_returns = np.array([up, down] * 31 + [0.0])
        daily_returns = np.concatenate((np.full(30, 0.002), recent_returns))
        history = price_history(daily_returns)
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        self.assertAlmostEqual(metrics["directional_efficiency_63d"], 0.0, places=12)

    def test_63d_metrics_are_nan_when_history_is_short(self) -> None:
        history = price_history(np.full(50, 0.01))
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        for column in (
            "max_daily_move_63d",
            "max_gap_63d",
            "max_1d_share_of_abs_move_63d",
            "directional_efficiency_63d",
            "positive_days_63d",
            "negative_days_63d",
            "positive_day_ratio_63d",
        ):
            self.assertTrue(math.isnan(metrics[column]))

    def test_126d_metrics_are_nan_when_history_is_short(self) -> None:
        history = price_history(np.full(100, 0.01))
        metrics = calculate_ticker_metrics(history, history.index[-1])
        assert metrics is not None
        for column in (
            "max_daily_move_126d",
            "max_gap_126d",
            "max_1d_share_of_abs_move_126d",
            "directional_efficiency_126d",
            "positive_days_126d",
            "negative_days_126d",
            "positive_day_ratio_126d",
        ):
            self.assertTrue(math.isnan(metrics[column]))


class RelativePriceMetricTests(unittest.TestCase):
    def test_aligned_relative_log_metrics(self) -> None:
        stock = price_history(np.full(140, 0.01))
        sector = price_history(np.full(140, 0.002))
        metrics = calculate_relative_price_metrics(stock, sector, stock.index[-1])
        daily_relative = math.log1p(0.01) - math.log1p(0.002)
        for horizon in (63, 126):
            self.assertAlmostEqual(
                metrics[f"relative_max_daily_move_{horizon}d"],
                abs(daily_relative),
            )
            self.assertAlmostEqual(
                metrics[f"relative_max_1d_share_of_abs_move_{horizon}d"],
                1 / horizon,
            )
            self.assertAlmostEqual(
                metrics[f"relative_directional_efficiency_{horizon}d"],
                1.0,
            )

    def test_offsetting_relative_log_returns_are_near_zero(self) -> None:
        up = 0.01
        down = 1.0 / (1.0 + up) - 1.0
        stock_returns = np.array([up, down] * 70)
        stock = price_history(stock_returns)
        sector = price_history(np.zeros(140))
        metrics = calculate_relative_price_metrics(stock, sector, stock.index[-1])
        for horizon in (63, 126):
            efficiency = metrics[f"relative_directional_efficiency_{horizon}d"]
            self.assertGreaterEqual(efficiency, 0.0)
            self.assertLessEqual(efficiency, 1.0)
        self.assertAlmostEqual(
            metrics["relative_directional_efficiency_126d"], 0.0, places=12
        )

    def test_zero_relative_denominator_is_nan(self) -> None:
        stock = price_history(np.full(140, 0.01))
        metrics = calculate_relative_price_metrics(stock, stock, stock.index[-1])
        for horizon in (63, 126):
            self.assertTrue(
                math.isnan(
                    metrics[f"relative_max_1d_share_of_abs_move_{horizon}d"]
                )
            )
            self.assertTrue(
                math.isnan(metrics[f"relative_directional_efficiency_{horizon}d"])
            )

    def test_history_shortage_and_date_mismatch_are_nan(self) -> None:
        stock = price_history(np.full(140, 0.01))
        short_sector = price_history(np.full(60, 0.002))
        short_metrics = calculate_relative_price_metrics(
            stock, short_sector, stock.index[-1]
        )
        self.assertTrue(math.isnan(short_metrics["relative_max_daily_move_63d"]))

        mismatched_sector = price_history(np.full(140, 0.002), end="2026-07-10")
        mismatch_metrics = calculate_relative_price_metrics(
            stock, mismatched_sector, stock.index[-1]
        )
        self.assertTrue(math.isnan(mismatch_metrics["relative_max_daily_move_63d"]))


class QualityValidationTests(unittest.TestCase):
    @staticmethod
    def frame(tickers: list[str], suspicious: set[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ticker": tickers,
                "return_21d": [3.0 if ticker in suspicious else 0.1 for ticker in tickers],
                "max_daily_move_21d": [0.2] * len(tickers),
                "max_gap_21d": [0.1] * len(tickers),
            }
        )

    def test_one_isolated_anomaly_is_excluded(self) -> None:
        metrics = self.frame(["GOOD", "BAD"], {"BAD"})
        excluded = validate_metric_dataframe(
            metrics,
            {
                "max_abs_daily_return_for_validation": 2.0,
                "max_isolated_price_anomalies": 1,
            },
        )
        remaining = metrics.loc[~metrics["ticker"].isin(excluded), "ticker"].tolist()
        self.assertEqual(excluded, ["BAD"])
        self.assertEqual(remaining, ["GOOD"])

    def test_more_than_configured_anomalies_stops(self) -> None:
        metrics = self.frame(["BAD1", "BAD2"], {"BAD1", "BAD2"})
        with self.assertRaises(RuntimeError):
            validate_metric_dataframe(
                metrics,
                {
                    "max_abs_daily_return_for_validation": 2.0,
                    "max_isolated_price_anomalies": 1,
                },
            )


class UniverseDistributionTests(unittest.TestCase):
    def test_percentiles_counts_and_sector_distribution(self) -> None:
        metrics = pd.DataFrame(
            {
                "sector": ["A", "A", "A", "B", "B"],
                "return_21d": [-0.30, -0.10, 0.0, 0.10, 0.30],
                "spy_relative_21d": [-0.09, -0.08, 0.0, 0.08, 0.09],
                "sector_relative_21d": [0.01, 0.02, 0.03, 0.04, 0.05],
                "return_63d": [0.10, 0.20, 0.30, 0.40, 0.50],
            }
        )
        universe, sectors = calculate_universe_distributions(metrics)

        self.assertAlmostEqual(universe["return_21d"]["p10"], -0.22)
        self.assertAlmostEqual(universe["return_21d"]["median"], 0.0)
        self.assertAlmostEqual(universe["return_21d"]["p90"], 0.22)
        self.assertAlmostEqual(universe["spy_relative_21d"]["median"], 0.0)
        self.assertAlmostEqual(universe["sector_relative_21d"]["median"], 0.03)
        self.assertAlmostEqual(universe["return_63d"]["median"], 0.30)
        self.assertEqual(universe["return_21d_gt_20pct_count"], 1)
        self.assertEqual(universe["return_21d_lt_minus_20pct_count"], 1)
        self.assertEqual(universe["abs_spy_relative_21d_gt_8pct_count"], 2)
        self.assertEqual(sectors["A"]["count"], 3)
        self.assertAlmostEqual(sectors["A"]["sector_relative_21d"]["median"], 0.02)
        self.assertAlmostEqual(sectors["A"]["return_63d"]["median"], 0.20)


def quiet_drift_config() -> dict[str, float | int]:
    return {
        "quiet_drift_tail_quantile": 0.10,
        "quiet_drift_max_candidates": 100,
        "quiet_drift_max_daily_move_63d": 0.07,
        "quiet_drift_max_gap_63d": 0.07,
        "quiet_drift_max_volume_ratio": 1.80,
        "quiet_drift_max_volatility_ratio": 1.50,
        "quiet_drift_max_single_day_share_63d": 0.20,
        "quiet_drift_min_directional_efficiency_63d": 0.25,
        "quiet_drift_max_daily_move_126d": 0.07,
        "quiet_drift_max_gap_126d": 0.07,
        "quiet_drift_max_single_day_share_126d": 0.20,
        "quiet_drift_min_directional_efficiency_126d": 0.25,
        "quiet_drift_min_relative_directional_efficiency_63d": 0.25,
        "quiet_drift_min_relative_directional_efficiency_126d": 0.25,
        "quiet_drift_max_relative_single_day_share_63d": 0.20,
        "quiet_drift_max_relative_single_day_share_126d": 0.20,
        "quiet_drift_sector_top_n": 1,
        "quiet_drift_sector_bottom_n": 1,
        "quiet_drift_sector_coverage_min_tail_distance": 0.50,
    }


def quiet_drift_metrics() -> pd.DataFrame:
    tickers = [f"T{i}" for i in range(10)]
    return pd.DataFrame(
        {
            "ticker": tickers,
            "company_name": [f"Company {i}" for i in range(10)],
            "sector": ["A"] * 5 + ["B"] * 5,
            "market_cap": [2_000_000_000] * 10,
            "market_data_date": ["2026-07-13"] * 10,
            "price": [100.0] * 10,
            "return_21d": [0.02] * 10,
            "return_63d": [-0.25, -0.18, -0.08, -0.02, 0.0, 0.02, 0.08, 0.12, 0.20, 0.28],
            "return_126d": [-0.08, -0.06, -0.04, -0.28, 0.0, 0.02, 0.30, 0.04, 0.06, 0.08],
            "spy_relative_63d": [-0.20, -0.15, -0.05, -0.01, 0.0, 0.01, 0.05, 0.08, 0.15, 0.22],
            "spy_relative_126d": [-0.05, -0.04, -0.03, -0.22, 0.0, 0.01, 0.24, 0.03, 0.04, 0.05],
            "sector_etf": ["XLA"] * 5 + ["XLB"] * 5,
            "sector_relative_63d": [-0.30, -0.20, -0.10, -0.05, 0.0, 0.02, 0.05, 0.10, 0.20, 0.30],
            "sector_relative_126d": [-0.05, -0.04, -0.03, -0.30, 0.0, 0.02, 0.30, 0.03, 0.04, 0.05],
            "max_daily_move_63d": [0.02] * 10,
            "max_gap_63d": [0.01] * 10,
            "max_1d_share_of_abs_move_63d": [0.08] * 10,
            "directional_efficiency_63d": [0.70] * 10,
            "positive_days_63d": [40] * 10,
            "negative_days_63d": [23] * 10,
            "positive_day_ratio_63d": [40 / 63] * 10,
            "relative_max_daily_move_63d": [0.015] * 10,
            "relative_max_1d_share_of_abs_move_63d": [0.08] * 10,
            "relative_directional_efficiency_63d": [0.70] * 10,
            "max_daily_move_126d": [0.025] * 10,
            "max_gap_126d": [0.015] * 10,
            "max_1d_share_of_abs_move_126d": [0.06] * 10,
            "directional_efficiency_126d": [0.65] * 10,
            "positive_days_126d": [75] * 10,
            "negative_days_126d": [51] * 10,
            "positive_day_ratio_126d": [75 / 126] * 10,
            "relative_max_daily_move_126d": [0.012] * 10,
            "relative_max_1d_share_of_abs_move_126d": [0.05] * 10,
            "relative_directional_efficiency_126d": [0.60] * 10,
            "volume_ratio_5d_vs_prev20d": [1.10] * 10,
            "volatility_ratio_20d_vs_prev120d": [1.10] * 10,
        }
    )


class QuietDriftSelectionTests(unittest.TestCase):
    def build(
        self,
        metrics: pd.DataFrame | None = None,
        config: dict[str, float | int] | None = None,
    ) -> pd.DataFrame:
        candidates, _, _ = build_quiet_drift_candidates(
            quiet_drift_metrics() if metrics is None else metrics,
            quiet_drift_config() if config is None else config,
        )
        validate_quiet_drift_dataframe(candidates, pd.Timestamp("2026-07-13"))
        return candidates

    def test_quiet_up_and_down_drifts_are_selected(self) -> None:
        candidates = self.build().set_index("ticker")
        self.assertEqual(candidates.loc["T9", "drift_direction"], "up")
        self.assertEqual(candidates.loc["T0", "drift_direction"], "down")

    def test_63d_and_126d_anchor_paths_are_selected(self) -> None:
        candidates = self.build().set_index("ticker")
        self.assertEqual(candidates.loc["T9", "anchor_horizon"], "63d")
        self.assertEqual(candidates.loc["T6", "anchor_horizon"], "126d")
        self.assertEqual(candidates.loc["T3", "anchor_horizon"], "126d")

    def assert_filtered(self, column: str, value: float) -> None:
        metrics = quiet_drift_metrics()
        metrics.loc[metrics["ticker"] == "T9", column] = value
        self.assertNotIn("T9", set(self.build(metrics)["ticker"]))

    def test_single_shock_is_filtered(self) -> None:
        self.assert_filtered("max_daily_move_63d", 0.071)

    def test_large_gap_is_filtered(self) -> None:
        self.assert_filtered("max_gap_63d", 0.071)

    def test_concentrated_single_day_share_is_filtered(self) -> None:
        self.assert_filtered("max_1d_share_of_abs_move_63d", 0.201)

    def test_recent_volume_spike_is_filtered(self) -> None:
        self.assert_filtered("volume_ratio_5d_vs_prev20d", 1.80)

    def test_high_volatility_is_filtered(self) -> None:
        self.assert_filtered("volatility_ratio_20d_vs_prev120d", 1.50)

    def test_low_directional_efficiency_is_filtered(self) -> None:
        self.assert_filtered("directional_efficiency_63d", 0.249)

    def test_low_relative_directional_efficiency_is_filtered(self) -> None:
        self.assert_filtered("relative_directional_efficiency_63d", 0.249)

    def test_126d_anchor_requires_126d_stock_and_relative_quietness(self) -> None:
        for column, value in (
            ("max_daily_move_126d", 0.071),
            ("max_gap_126d", 0.071),
            ("max_1d_share_of_abs_move_126d", 0.201),
            ("directional_efficiency_126d", 0.249),
            ("relative_max_1d_share_of_abs_move_126d", 0.201),
            ("relative_directional_efficiency_126d", 0.249),
        ):
            with self.subTest(column=column):
                metrics = quiet_drift_metrics()
                metrics.loc[metrics["ticker"] == "T6", column] = value
                self.assertNotIn("T6", set(self.build(metrics)["ticker"]))

    def test_126d_anchor_requires_same_direction(self) -> None:
        metrics = quiet_drift_metrics()
        metrics.loc[metrics["ticker"] == "T6", "sector_relative_63d"] = -0.05
        self.assertNotIn("T6", set(self.build(metrics)["ticker"]))

    def test_same_direction_126d_anchor_is_selected(self) -> None:
        candidates = self.build().set_index("ticker")
        self.assertEqual(candidates.loc["T6", "trend_consistency"], "same_direction")

    def test_63d_anchor_allows_recent_regime_change(self) -> None:
        metrics = quiet_drift_metrics()
        metrics.loc[metrics["ticker"] == "T9", "sector_relative_126d"] = -0.05
        candidates = self.build(metrics).set_index("ticker")
        self.assertEqual(candidates.loc["T9", "anchor_horizon"], "63d")
        self.assertEqual(
            candidates.loc["T9", "trend_consistency"],
            "recent_regime_change",
        )

    def test_missing_required_data_is_filtered(self) -> None:
        metrics = quiet_drift_metrics()
        metrics.loc[metrics["ticker"] == "T9", "max_gap_63d"] = np.nan
        self.assertNotIn("T9", set(self.build(metrics)["ticker"]))

    def test_global_and_sector_reasons_are_merged(self) -> None:
        candidates = self.build()
        row = candidates.loc[candidates["ticker"] == "T6"].iloc[0]
        self.assertEqual(row["selection_bucket"], "global_tail")
        self.assertIn("quiet_drift_sector_relative_126d_top", row["selection_reason"])
        self.assertIn("quiet_drift_sector_coverage_top", row["selection_reason"])
        self.assertEqual(candidates["ticker"].value_counts().max(), 1)

    def test_single_member_sector_gets_only_directional_coverage(self) -> None:
        metrics = quiet_drift_metrics()
        metrics.loc[metrics["ticker"] == "T9", "sector"] = "SoloUp"
        metrics.loc[metrics["ticker"] == "T0", "sector"] = "SoloDown"
        candidates = self.build(metrics).set_index("ticker")
        up_reason = candidates.loc["T9", "selection_reason"]
        down_reason = candidates.loc["T0", "selection_reason"]
        self.assertIn("quiet_drift_sector_coverage_top", up_reason)
        self.assertNotIn("quiet_drift_sector_coverage_bottom", up_reason)
        self.assertIn("quiet_drift_sector_coverage_bottom", down_reason)
        self.assertNotIn("quiet_drift_sector_coverage_top", down_reason)

    def test_single_member_sector_with_zero_anchor_is_not_covered(self) -> None:
        metrics = quiet_drift_metrics()
        metrics.loc[metrics["ticker"] == "T4", "sector"] = "SoloZero"
        candidates = self.build(metrics)
        self.assertNotIn("T4", set(candidates["ticker"]))

    def test_multi_member_sector_coverage_has_no_overlap(self) -> None:
        candidates = self.build()
        top = set(
            candidates.loc[
                candidates["selection_reason"].str.contains(
                    "quiet_drift_sector_coverage_top", regex=False
                ),
                "ticker",
            ]
        )
        bottom = set(
            candidates.loc[
                candidates["selection_reason"].str.contains(
                    "quiet_drift_sector_coverage_bottom", regex=False
                ),
                "ticker",
            ]
        )
        self.assertFalse(top & bottom)

    def test_sector_coverage_minimum_tail_distance_guard(self) -> None:
        self.assertFalse(sector_coverage_strength_allowed("sector_coverage", 0.49, 0.50))
        self.assertTrue(sector_coverage_strength_allowed("sector_coverage", 0.50, 0.50))
        self.assertTrue(sector_coverage_strength_allowed("global_tail", 0.00, 0.50))

    def test_maximum_and_ranking_are_deterministic(self) -> None:
        config = quiet_drift_config()
        config["quiet_drift_max_candidates"] = 3
        original = self.build(config=config)
        shuffled = quiet_drift_metrics().sample(frac=1.0, random_state=7)
        repeated = self.build(shuffled, config)
        self.assertEqual(len(original), 3)
        self.assertEqual(original["ticker"].tolist(), repeated["ticker"].tolist())
        self.assertEqual(original["rank"].tolist(), [1, 2, 3])

    def test_empty_result_writes_header_and_row_count_matches(self) -> None:
        config = quiet_drift_config()
        config["quiet_drift_min_directional_efficiency_63d"] = 1.01
        candidates = self.build(config=config)
        self.assertTrue(candidates.empty)
        self.assertEqual(candidates.columns.tolist(), QUIET_DRIFT_OUTPUT_COLUMNS)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quiet_drift.csv"
            write_dataframe_csv(candidates, path)
            loaded = pd.read_csv(path)
        self.assertEqual(len(loaded), len(candidates))
        self.assertEqual(loaded.columns.tolist(), QUIET_DRIFT_OUTPUT_COLUMNS)


class RepositoryTextTests(unittest.TestCase):
    def test_chatgpt_prompt_is_within_custom_gpt_limit(self) -> None:
        prompt = Path("CHATGPT_PROMPT.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(prompt), 8_000)

    def test_changed_text_files_have_no_hidden_directional_unicode(self) -> None:
        forbidden = {
            *range(0x202A, 0x202F),
            *range(0x2066, 0x206A),
            0x200E,
            0x200F,
            0xFEFF,
        }
        paths = (
            Path("src/screen.py"),
            Path("scripts/check_status.py"),
            Path("tests/test_screen.py"),
            Path("config.yml"),
            Path("README.md"),
            Path("CHATGPT_PROMPT.md"),
        )
        violations = [
            (str(path), index, f"U+{ord(character):04X}")
            for path in paths
            for index, character in enumerate(path.read_text(encoding="utf-8"))
            if ord(character) in forbidden
        ]
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
