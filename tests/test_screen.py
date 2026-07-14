from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from src.screen import (
    calculate_ticker_metrics,
    calculate_universe_distributions,
    validate_metric_dataframe,
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


if __name__ == "__main__":
    unittest.main()
