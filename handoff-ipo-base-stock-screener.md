# Handoff: 打新底仓选股筛选器

## Context

The user is preparing for ChangXin Memory / 长鑫存储 IPO participation. In public IPO materials and exchange/regulatory records, the actual proposed listing entity is **长鑫科技集团股份有限公司**, targeting the **SSE STAR Market / 科创板**. As of the prior discussion date, it had passed the SSE listing committee and entered CSRC registration, but no official subscription date or listing date had been announced.

The user has an A-share brokerage account but received this brokerage warning:

> 科创板权限：前20个交易日日均不在人民币50万元以上

This means they have not yet met the STAR Market investor suitability asset threshold. We clarified that STAR Market participation requires separate conditions:

- Personal investor STAR Market permission: normally 20 trading-day daily average assets >= RMB 500,000 plus 24 months trading experience.
- New-share subscription market value quota: for SSE/STAR online subscription, the investor needs Shanghai-market non-restricted A-share/DR market value, calculated on the T-2 day prior 20 trading-day daily average window.
- For STAR Market online subscription, quota is commonly **RMB 5,000 Shanghai-market daily average market value = 500 shares quota**.
- Each IPO has its own single-account subscription cap in the issuance announcement. Once an account can subscribe up to that cap, additional market value does not improve the winning probability for that one IPO.
- Family-member accounts can improve household aggregate odds only if each family member independently and compliantly owns/controls the account, meets STAR permissions, and has its own Shanghai-market quota. Account borrowing/lending is not recommended and may be illegal/non-compliant.

The user then asked how to implement a practical stock-selection workflow for the quote:

> 为了打新资格持仓，核心是降低波动，不是赌这只持仓股票上涨。可以分散到几只流动性好、波动相对小、基本面比较稳的沪市大盘股/高股息类股票。不要为了打新买高波动题材股。

The immediate next-session focus is likely to implement or refine a local screener for “打新底仓” candidates.

## Local Repository Findings

The user asked whether any existing local repos are suitable. We reviewed `/root/repos/repo_summary.md` and some repo files.

Relevant repo summary references:

- `/root/repos/repo_summary.md`
- Financial section starts around `/root/repos/repo_summary.md:220`
- It lists these local stock/financial repos:
  - `Claude-Code-Stock-Deep-Research-Agent`
  - `claudesdk-stock-chat`
  - `easy_investment_Agent_crewai`
  - `crewai_stock_analysis_system`
  - `claudesdk-financial-chart-chat`

Working conclusion:

1. Best main base: `/root/repos/endifcoin/easy_investment_Agent_crewai/stock_analysis_a_stock`
   - It already has `akshare`, `pandas`, `numpy` dependencies in:
     - `/root/repos/endifcoin/easy_investment_Agent_crewai/stock_analysis_a_stock/pyproject.toml`
   - Its README says it supports A-shares and Shanghai codes:
     - `/root/repos/endifcoin/easy_investment_Agent_crewai/README.md`
   - It has an AKShare-based data tool:
     - `/root/repos/endifcoin/easy_investment_Agent_crewai/stock_analysis_a_stock/src/a_stock_analysis/tools/a_stock_data_tool.py`
   - It is relatively lightweight compared with the other agent/reporting systems.

2. Useful reference, not main base: `/root/repos/endifcoin/claudesdk-financial-chart-chat`
   - README says it uses Baostock as primary data source plus AKShare fallback.
   - Relevant files:
     - `/root/repos/endifcoin/claudesdk-financial-chart-chat/README.md`
     - `/root/repos/endifcoin/claudesdk-financial-chart-chat/.claude/skills/financial-charts/data_fetcher.py`
     - `/root/repos/endifcoin/claudesdk-financial-chart-chat/.claude/skills/financial-charts/financial_charts.py`
   - This repo is for financial chart generation, not screening, but its data fetching approach may be reusable.

3. Less suitable:
   - `/root/repos/endifcoin/crewai_stock_analysis_system`
     - Has AkShare tools and batch analyzer, but is heavier and oriented toward multi-agent reports.
     - Relevant files:
       - `/root/repos/endifcoin/crewai_stock_analysis_system/src/tools/akshare_tools.py`
       - `/root/repos/endifcoin/crewai_stock_analysis_system/src/utils/batch_analyzer.py`
   - `/root/repos/endifcoin/Claude-Code-Stock-Deep-Research-Agent`
     - Good due diligence framework, not quantitative screening.
   - `/root/repos/endifcoin/claudesdk-stock-chat`
     - Web research assistant, not a screener.

## Recommended Implementation

Add an independent script to the best base repo:

`/root/repos/endifcoin/easy_investment_Agent_crewai/stock_analysis_a_stock/scripts/ipo_base_stock_screener.py`

Purpose:

Create a transparent screening/reporting tool for Shanghai-main-board “打新底仓” candidates. It should be a decision-support screener, not an investment recommendation engine.

Suggested output:

- CSV and Markdown outputs, e.g.:
  - `outputs/ipo_base_stock_candidates.csv`
  - `outputs/ipo_base_stock_candidates.md`

Candidate universe:

- A-share stocks only.
- Keep Shanghai main board codes, likely code prefixes:
  - `600`
  - `601`
  - `603`
  - `605`
- Exclude STAR Market `688` because the user may not yet have STAR permission and the purpose is Shanghai market value quota, not STAR exposure.
- Exclude ST / *ST / delisting-risk names.
- Exclude recent listings if insufficient trading history.

Core metrics to compute:

- Liquidity:
  - 20-day average turnover amount.
  - Optional: 60-day average turnover amount.
- Size:
  - total market cap and/or free-float market cap if available.
- Volatility:
  - 60-day realized volatility.
  - 120-day realized volatility.
  - Optional: 250-day realized volatility.
- Drawdown:
  - max drawdown over 120 or 250 trading days.
- Dividend / quality:
  - dividend yield if available.
  - dividend continuity if available.
  - ROE if available.
  - debt ratio if available.
  - operating cash flow proxy if easy to fetch.
- Basic valuation:
  - PE/PB as context, not as the central objective.

Suggested ranking philosophy:

- Optimize for low volatility, liquidity, and stability.
- Do not overfit for highest yield; high yield can be caused by falling prices.
- Do not frame output as “buy”. Use categories like:
  - `候选`
  - `观察`
  - `剔除`
  - `数据不足`

Possible scoring structure:

- Liquidity score: 20%
- Size score: 15%
- Low volatility score: 30%
- Drawdown control score: 15%
- Dividend/quality score: 20%

Use robust percentiles/ranks rather than hard-coded absolute thresholds where possible, but include conservative hard filters:

- average turnover amount >= a reasonable minimum
- trading history >= required lookback
- ST excluded
- market cap above a threshold

## Important Financial / Compliance Framing

Keep the response careful:

- Do not provide personalized financial advice.
- Do not promise higher returns or IPO winning.
- Make clear that holding stocks for IPO quota has price-risk and may lose more than IPO benefit.
- Explain that this tool helps find lower-volatility candidates for market-value quota only.
- Remind that the user should verify brokerage-specific calculation rules and market data freshness.

## Open Questions For The Next Agent

Ask only if needed; otherwise make reasonable defaults:

- Does the user want the script implemented now?
- Preferred data source: AKShare only, or borrow Baostock code from `claudesdk-financial-chart-chat`?
- Desired holding amount / target IPO subscription cap buffer?
- Is the user okay with outputs under the repo, e.g. `outputs/`, or should outputs go to `/tmp`?

Reasonable defaults:

- Use AKShare first because the chosen base repo already depends on it.
- Save outputs under the base repo’s `outputs/` directory if implementing.
- Start with 60/120/250-day lookbacks.
- Keep implementation standalone, no CrewAI integration unless requested.

## Suggested Skills

- `openai-docs`: only if the next agent needs OpenAI product/API guidance. Probably not needed for this task.
- `firecrawl-search` or `tavily-search`: use if the next agent needs fresh official market-rule citations or current IPO status. Since IPO/rules can change, browse official sources if answering current-status questions.
- No special skill is needed for local implementation. Use normal repo inspection and `apply_patch`.

## Suggested Next Steps

1. Re-read:
   - `/root/repos/repo_summary.md`
   - `/root/repos/endifcoin/easy_investment_Agent_crewai/stock_analysis_a_stock/pyproject.toml`
   - `/root/repos/endifcoin/easy_investment_Agent_crewai/stock_analysis_a_stock/src/a_stock_analysis/tools/a_stock_data_tool.py`
   - optionally `/root/repos/endifcoin/claudesdk-financial-chart-chat/.claude/skills/financial-charts/data_fetcher.py`

2. Implement `scripts/ipo_base_stock_screener.py` in the chosen repo.

3. Add a small README or usage note if the script is non-obvious.

4. Run the script if dependencies and network/data APIs are available. If network is restricted or AKShare fetch fails, report that clearly and leave the implementation ready.

5. Do not make a list of specific stocks unless data was fetched and the output includes clear risk disclaimers.
