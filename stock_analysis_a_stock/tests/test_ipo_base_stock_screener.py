import importlib.util
import pathlib
import sys
import tempfile
import unittest


SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "ipo_base_stock_screener.py"
)

spec = importlib.util.spec_from_file_location("ipo_base_stock_screener", SCRIPT_PATH)
screener = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = screener
spec.loader.exec_module(screener)


class IpoBaseStockScreenerTest(unittest.TestCase):
    def test_screen_candidates_filters_unfit_base_holdings_and_ranks_stable_names(self):
        stocks = [
            {
                "code": "600001",
                "name": "低波银行",
                "total_market_cap": 180_000_000_000,
                "float_market_cap": 120_000_000_000,
                "pe": 7.5,
                "pb": 0.8,
                "dividend_yield": 4.2,
            },
            {
                "code": "600002",
                "name": "高波制造",
                "total_market_cap": 80_000_000_000,
                "float_market_cap": 55_000_000_000,
                "pe": 18.0,
                "pb": 2.1,
                "dividend_yield": 1.0,
            },
            {
                "code": "688001",
                "name": "科创样本",
                "total_market_cap": 90_000_000_000,
                "float_market_cap": 60_000_000_000,
            },
            {
                "code": "600003",
                "name": "*ST风险",
                "total_market_cap": 70_000_000_000,
                "float_market_cap": 40_000_000_000,
            },
            {
                "code": "600004",
                "name": "历史不足",
                "total_market_cap": 100_000_000_000,
                "float_market_cap": 70_000_000_000,
            },
            {
                "code": "600005",
                "name": "市值缺失",
            },
            {
                "code": "600006",
                "name": "小市值",
                "total_market_cap": 2_000_000_000,
            },
        ]
        histories = {
            "600001": [
                {"close": 10 + i * 0.01, "amount": 1_200_000_000}
                for i in range(260)
            ],
            "600002": [
                {"close": 10 + ((-1) ** i) * 0.25 + i * 0.03, "amount": 600_000_000}
                for i in range(260)
            ],
            "688001": [
                {"close": 10 + i * 0.01, "amount": 1_000_000_000}
                for i in range(260)
            ],
            "600003": [
                {"close": 10 + i * 0.01, "amount": 1_000_000_000}
                for i in range(260)
            ],
            "600004": [
                {"close": 10 + i * 0.01, "amount": 1_000_000_000}
                for i in range(80)
            ],
            "600005": [
                {"close": 10 + i * 0.01, "amount": 1_100_000_000}
                for i in range(260)
            ],
            "600006": [
                {"close": 10 + i * 0.01, "amount": 1_100_000_000}
                for i in range(260)
            ],
        }

        result = screener.screen_candidates(stocks, histories)

        codes = [row["code"] for row in result.rows]
        self.assertEqual(codes, ["600001", "600005", "600002"])
        self.assertEqual(result.rows[0]["rating"], "候选")
        self.assertGreater(result.rows[0]["score"], result.rows[1]["score"])
        self.assertFalse(result.rows[1]["market_cap_checked"])
        self.assertLess(result.rows[0]["volatility_120d"], result.rows[2]["volatility_120d"])

        rejected = {row["code"]: row["reason"] for row in result.rejected}
        self.assertIn("not_shanghai_main_board", rejected["688001"])
        self.assertIn("st_or_delisting_risk", rejected["600003"])
        self.assertIn("insufficient_history", rejected["600004"])
        self.assertIn("market_cap_too_small", rejected["600006"])

    def test_writes_csv_and_markdown_reports_with_risk_framing(self):
        rows = [
            {
                "rating": "候选",
                "score": 88.8,
                "code": "600001",
                "name": "低波银行",
                "avg_amount_20d": 1_200_000_000,
                "avg_amount_60d": 1_100_000_000,
                "volatility_60d": 0.08,
                "volatility_120d": 0.10,
                "max_drawdown_120d": 0.05,
                "total_market_cap": 180_000_000_000,
                "float_market_cap": 120_000_000_000,
                "market_cap_checked": True,
                "dividend_yield": 4.2,
                "pe": 7.5,
                "pb": 0.8,
                "history_days": 260,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            csv_path = tmp_path / "candidates.csv"
            md_path = tmp_path / "candidates.md"

            screener.write_csv(csv_path, rows)
            screener.write_markdown(md_path, rows, [], "2026-06-09 10:00:00", "Baostock")

            csv_text = csv_path.read_text(encoding="utf-8-sig")
            md_text = md_path.read_text(encoding="utf-8")

        self.assertIn("rating,score,code,name", csv_text)
        self.assertIn("600001", csv_text)
        self.assertIn("不构成投资建议", md_text)
        self.assertIn("持仓股票可能下跌", md_text)
        self.assertIn("数据源：Baostock", md_text)

    def test_percentile_scores_do_not_prefer_input_order_for_ties(self):
        self.assertEqual(
            screener.percentile_scores([1.0, 1.0, 1.0], higher_is_better=True),
            [50.0, 50.0, 50.0],
        )
        self.assertEqual(
            screener.percentile_scores([1.0, 2.0, 2.0], higher_is_better=True),
            [0.0, 75.0, 75.0],
        )

    def test_calculate_metrics_includes_multi_period_returns(self):
        history = [{"close": 100 + index, "amount": 1_000_000_000} for index in range(130)]

        metrics = screener.calculate_metrics(history)

        self.assertAlmostEqual(metrics["return_5d"], 229 / 224 - 1)
        self.assertAlmostEqual(metrics["return_20d"], 229 / 209 - 1)
        self.assertAlmostEqual(metrics["return_60d"], 229 / 169 - 1)
        self.assertAlmostEqual(metrics["return_120d"], 229 / 109 - 1)

    def test_basket_return_summary_uses_equal_weight_and_skips_missing_values(self):
        baskets = {
            "稳健底仓": [
                {"return_5d": 0.10, "return_20d": 0.20},
                {"return_5d": -0.05, "return_20d": None},
            ]
        }

        summary = screener.build_basket_return_summary(baskets)

        self.assertEqual(summary[0]["basket"], "稳健底仓")
        self.assertEqual(summary[0]["count"], 2)
        self.assertAlmostEqual(summary[0]["return_5d"], 0.025)
        self.assertAlmostEqual(summary[0]["return_20d"], 0.20)

    def test_manual_codes_create_minimal_stock_universe(self):
        stocks = screener.stocks_from_codes("sh600000, 601398.SH,601988")

        self.assertEqual([stock["code"] for stock in stocks], ["600000", "601398", "601988"])
        self.assertTrue(all(stock["source"] == "manual_codes" for stock in stocks))
        self.assertTrue(all(stock["total_market_cap"] is None for stock in stocks))

    def test_baostock_code_and_date_formatting(self):
        self.assertEqual(screener.normalize_code("sh.600000"), "600000")
        self.assertEqual(screener.normalize_code("600000.SH"), "600000")
        self.assertEqual(screener.baostock_code("600000.SH"), "sh.600000")
        self.assertEqual(screener.date_for_baostock("20260609"), "2026-06-09")

    def test_make_stock_record_normalizes_code_and_name(self):
        stock = screener.make_stock_record("sh.600000", "浦发银行", "baostock")

        self.assertEqual(stock["code"], "600000")
        self.assertEqual(stock["name"], "浦发银行")
        self.assertEqual(stock["source"], "baostock")

    def test_build_baskets_selects_diversified_candidates(self):
        rows = [
            {
                "code": "600900",
                "name": "长江电力",
                "industry": "D44电力、热力生产和供应业",
                "score": 80,
                "avg_amount_20d": 3_000_000_000,
                "volatility_120d": 0.12,
                "max_drawdown_120d": 0.08,
                "dividend_yield": 3.8,
                "roe": 15.0,
                "debt_to_asset": 45.0,
                "cfo_to_np": 1.2,
                "total_market_cap": 600_000_000_000,
            },
            {
                "code": "601398",
                "name": "工商银行",
                "industry": "J66货币金融服务",
                "score": 75,
                "avg_amount_20d": 2_000_000_000,
                "volatility_120d": 0.16,
                "max_drawdown_120d": 0.12,
                "dividend_yield": 5.0,
                "roe": 11.0,
                "debt_to_asset": 92.0,
                "cfo_to_np": 0.0,
                "total_market_cap": 2_000_000_000_000,
            },
            {
                "code": "601988",
                "name": "中国银行",
                "industry": "J66货币金融服务",
                "score": 73,
                "avg_amount_20d": 1_600_000_000,
                "volatility_120d": 0.17,
                "max_drawdown_120d": 0.10,
                "dividend_yield": 4.8,
                "roe": 10.0,
                "debt_to_asset": 92.0,
                "cfo_to_np": 0.0,
                "total_market_cap": 1_500_000_000_000,
            },
            {
                "code": "600519",
                "name": "贵州茅台",
                "industry": "C15酒、饮料和精制茶制造业",
                "score": 68,
                "avg_amount_20d": 6_000_000_000,
                "volatility_120d": 0.24,
                "max_drawdown_120d": 0.19,
                "dividend_yield": 2.5,
                "roe": 28.0,
                "debt_to_asset": 20.0,
                "cfo_to_np": 1.1,
                "total_market_cap": 2_200_000_000_000,
            },
        ]

        baskets = screener.build_baskets(rows, per_basket=3, max_per_industry=1)

        self.assertIn("大盘核心", baskets)
        self.assertIn("稳健底仓", baskets)
        self.assertIn("高股息价值", baskets)
        stable_codes = [row["code"] for row in baskets["稳健底仓"]]
        self.assertIn("600900", stable_codes)
        financial_codes = [
            row["code"]
            for row in baskets["高股息价值"]
            if row["industry"] == "J66货币金融服务"
        ]
        self.assertLessEqual(len(financial_codes), 1)

    def test_final_picks_skip_duplicates_and_continue_within_basket(self):
        baskets = {
            "大盘核心": [{"code": "600900"}, {"code": "600036"}],
            "稳健底仓": [{"code": "600900"}, {"code": "601988"}, {"code": "601398"}],
        }

        final = screener.build_final_picks(baskets, per_basket=2)

        self.assertEqual([row["code"] for row in final], ["600900", "600036", "601988", "601398"])
        self.assertEqual([row["source_basket"] for row in final], ["大盘核心", "大盘核心", "稳健底仓", "稳健底仓"])


if __name__ == "__main__":
    unittest.main()
