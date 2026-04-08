from crewai.tools import BaseTool
from typing import Any, Optional, Type
from pydantic import BaseModel, Field
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta


class MarketSentimentToolSchema(BaseModel):
    """市场情绪工具输入参数"""
    stock_code: str = Field(..., description="股票代码，如：000001.SZ或600519.SH")
    sentiment_type: str = Field(..., description="情绪类型：flow（资金流向）、news（新闻情绪）、technical（技术情绪）")


class MarketSentimentTool(BaseTool):
    name: str = "Market sentiment analysis tool"
    description: str = "分析A股市场情绪，包括资金流向、新闻情绪和技术情绪"
    args_schema: Type[BaseModel] = MarketSentimentToolSchema

    def _run(self, stock_code: str, sentiment_type: str = "flow", **kwargs) -> Any:
        """执行市场情绪分析"""
        try:
            if sentiment_type == "flow":
                return self._analyze_capital_flow(stock_code)
            elif sentiment_type == "news":
                return self._analyze_news_sentiment(stock_code)
            elif sentiment_type == "technical":
                return self._analyze_technical_sentiment(stock_code)
            else:
                raise ValueError(f"不支持的情绪类型: {sentiment_type}")
        except Exception as e:
            return f"市场情绪分析失败: {str(e)}"

    def _analyze_capital_flow(self, stock_code: str) -> str:
        """分析资金流向"""
        try:
            # 确定市场类型
            if stock_code.endswith('.SZ'):
                market = "sz"
            elif stock_code.endswith('.SH'):
                market = "sh"
            else:
                return "无效的股票代码格式"

            code = stock_code.split('.')[0]

            result = f"""
股票 {stock_code} 资金流向分析：

=== 北向资金流向 ===
"""
            try:
                # 获取北向资金持股数据
                df = ak.stock_hsgt_north_net_flow_in()
                if not df.empty:
                    latest_flow = df.iloc[-1]
                    result += f"今日北向资金净流入：{latest_flow['净流入-北向']:,.0f}万元\n"
                    result += f"北向资金情绪：{'积极流入' if latest_flow['净流入-北向'] > 0 else '流出中'}\n"
            except:
                result += "北向资金数据获取失败\n"

            result += "\n=== 行业资金流向 ===\n"
            try:
                # 获取行业资金流向
                df = ak.stock_sector_fund_flow_rank()
                if not df.empty:
                    top_sectors = df.head(5)
                    result += "今日资金流入前5行业：\n"
                    for _, row in top_sectors.iterrows():
                        result += f"  • {row['名称']}：{row['净流入-主力']:.0f}万元\n"
            except:
                result += "行业资金数据获取失败\n"

            result += "\n=== 市场整体情绪 ===\n"
            try:
                # 获取市场涨跌情况
                df = ak.stock_zh_a_spot()
                if not df.empty:
                    up_count = len(df[df['涨跌幅'] > 0])
                    down_count = len(df[df['涨跌幅'] < 0])
                    total_count = len(df)

                    up_ratio = up_count / total_count * 100
                    result += f"上涨股票数：{up_count}只 ({up_ratio:.1f}%)\n"
                    result += f"下跌股票数：{down_count}只 ({100-up_ratio:.1f}%)\n"

                    if up_ratio > 70:
                        market_sentiment = "🔥 极度乐观"
                    elif up_ratio > 60:
                        market_sentiment = "😊 偏乐观"
                    elif up_ratio > 40:
                        market_sentiment = "😐 中性"
                    elif up_ratio > 30:
                        market_sentiment = "😟 偏悲观"
                    else:
                        market_sentiment = "😰 极度悲观"

                    result += f"市场情绪：{market_sentiment}\n"
            except:
                result += "市场情绪数据获取失败\n"

            # 分析个股资金流向（基于成交量和价格变化）
            result += "\n=== 个股资金流向分析 ===\n"
            try:
                df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                       start_date=(datetime.now() - timedelta(days=5)).strftime('%Y%m%d'),
                                       end_date=datetime.now().strftime('%Y%m%d'),
                                       adjust="qfq")

                if not df.empty and len(df) >= 2:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2]

                    # 计算量比（今日成交量 / 昨日成交量）
                    volume_ratio = latest['成交量'] / prev['成交量'] if prev['成交量'] > 0 else 1

                    # 价格变化
                    price_change = (latest['收盘'] - prev['收盘']) / prev['收盘'] * 100

                    result += f"量比：{volume_ratio:.2f}倍\n"
                    result += f"价格变动：{price_change:+.2f}%\n"

                    # 资金流向判断
                    if volume_ratio > 1.5 and price_change > 2:
                        flow_status = "💰 资金积极流入"
                    elif volume_ratio > 1.2 and price_change > 0:
                        flow_status = "📈 资金温和流入"
                    elif volume_ratio < 0.8 and price_change < -1:
                        flow_status = "📉 资金流出"
                    elif volume_ratio > 1.5 and price_change < 0:
                        flow_status = "🔄 资金分歧较大"
                    else:
                        flow_status = "➡️ 资金流向平稳"

                    result += f"资金流向：{flow_status}\n"
            except:
                result += "个股资金流向分析失败\n"

            return result

        except Exception as e:
            return f"资金流向分析失败: {str(e)}"

    def _analyze_news_sentiment(self, stock_code: str) -> str:
        """分析新闻情绪"""
        try:
            # 确定市场类型
            if stock_code.endswith('.SZ'):
                market = "sz"
            elif stock_code.endswith('.SH'):
                market = "sh"
            else:
                return "无效的股票代码格式"

            code = stock_code.split('.')[0]

            result = f"""
股票 {stock_code} 新闻情绪分析：

=== 市场热点追踪 ===
"""
            try:
                # 获取市场热点
                df = ak.stock_news_em()
                if not df.empty:
                    hot_topics = df.head(5)
                    result += "今日市场热点：\n"
                    for _, row in hot_topics.iterrows():
                        if hasattr(row, '标题') and hasattr(row, '发布时间'):
                            result += f"  • {row['标题']} ({row['发布时间']})\n"
            except:
                result += "市场热点数据获取失败\n"

            result += "\n=== 政策消息影响 ===\n"
            try:
                # 获取财经新闻
                df = ak.stock_news_jrj()
                if not df.empty:
                    policy_news = [row for _, row in df.iterrows() if '政策' in str(row.get('标题', '')) or '监管' in str(row.get('标题', ''))]
                    if policy_news:
                        result += "相关政策消息：\n"
                        for news in policy_news[:3]:
                            result += f"  • {news.get('标题', '无标题')}\n"
                    else:
                        result += "暂无重大相关政策消息\n"
            except:
                result += "政策消息获取失败\n"

            result += "\n=== 情绪指标综合 ===\n"

            # 基于市场数据计算情绪指标
            try:
                df = ak.stock_zh_a_spot()
                if not df.empty:
                    # 计算市场广度指标
                    advancers = len(df[df['涨跌幅'] > 0])
                    decliners = len(df[df['涨跌幅'] < 0])
                    breadth_ratio = advancers / (advancers + decliners) if (advancers + decliners) > 0 else 0.5

                    # 计算成交量变化
                    total_volume = df['成交量'].sum()
                    result += f"市场广度：{breadth_ratio:.2f}\n"
                    result += f"总成交量：{total_volume:,}\n"

                    # 恐慌贪婪指数简化版
                    if breadth_ratio > 0.7:
                        fear_greed_index = "🤑 贪婪"
                    elif breadth_ratio > 0.5:
                        fear_greed_index = "😊 乐观"
                    elif breadth_ratio > 0.3:
                        fear_greed_index = "😐 中性"
                    elif breadth_ratio > 0.2:
                        fear_greed_index = "😨 恐慌"
                    else:
                        fear_greed_index = "😱 极度恐慌"

                    result += f"市场情绪指数：{fear_greed_index}\n"
            except:
                result += "情绪指标计算失败\n"

            result += "\n=== 风险提示 ===\n"
            result += "• 注意市场整体情绪波动风险\n"
            result += "• 关注政策变化对板块的影响\n"
            result += "• 建议结合基本面分析决策\n"

            return result

        except Exception as e:
            return f"新闻情绪分析失败: {str(e)}"

    def _analyze_technical_sentiment(self, stock_code: str) -> str:
        """分析技术情绪"""
        try:
            # 确定市场类型
            if stock_code.endswith('.SZ'):
                market = "sz"
            elif stock_code.endswith('.SH'):
                market = "sh"
            else:
                return "无效的股票代码格式"

            code = stock_code.split('.')[0]

            # 获取历史数据
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                   start_date=(datetime.now() - timedelta(days=30)).strftime('%Y%m%d'),
                                   end_date=datetime.now().strftime('%Y%m%d'),
                                   adjust="qfq")

            if df.empty:
                return f"未找到股票 {stock_code} 的历史数据"

            result = f"""
股票 {stock_code} 技术情绪分析：

=== 技术指标分析 ===
"""

            # 计算技术指标
            df['MA5'] = df['收盘'].rolling(window=5).mean()
            df['MA10'] = df['收盘'].rolling(window=10).mean()
            df['MA20'] = df['收盘'].rolling(window=20).mean()
            df['MA30'] = df['收盘'].rolling(window=30).mean()

            # RSI计算
            df['RSI'] = self._calculate_rsi(df['收盘'], 14)

            # MACD计算
            df['EMA12'] = df['收盘'].ewm(span=12, adjust=False).mean()
            df['EMA26'] = df['收盘'].ewm(span=26, adjust=False).mean()
            df['MACD'] = df['EMA12'] - df['EMA26']
            df['SIGNAL'] = df['MACD'].ewm(span=9, adjust=False).mean()
            df['HIST'] = df['MACD'] - df['SIGNAL']

            latest = df.iloc[-1]
            prev = df.iloc[-2]

            # 价格趋势
            price_trend = "📈 上升趋势" if latest['收盘'] > latest['MA20'] and latest['MA5'] > latest['MA20'] else \
                        "📉 下降趋势" if latest['收盘'] < latest['MA20'] and latest['MA5'] < latest['MA20'] else \
                        "➡️ 震荡走势"

            result += f"价格趋势：{price_trend}\n"
            result += f"当前价格：{latest['收盘']:.2f}\n"
            result += f"MA5：{latest['MA5']:.2f}\n"
            result += f"MA20：{latest['MA20']:.2f}\n"

            # RSI分析
            rsi_value = latest['RSI']
            if pd.isna(rsi_value):
                rsi_sentiment = "数据不足"
            elif rsi_value > 70:
                rsi_sentiment = "⚠️ 超买状态"
            elif rsi_value < 30:
                rsi_sentiment = "💡 超卖状态"
            elif rsi_value > 60:
                rsi_sentiment = "🔥 强势区域"
            elif rsi_value < 40:
                rsi_sentiment = "❄️ 弱势区域"
            else:
                rsi_sentiment = "😐 正常区域"

            result += f"RSI(14)：{rsi_value:.2f} ({rsi_sentiment})\n"

            # MACD分析
            macd_signal = "📈 金叉信号" if latest['MACD'] > latest['SIGNAL'] and prev['MACD'] <= prev['SIGNAL'] else \
                         "📉 死叉信号" if latest['MACD'] < latest['SIGNAL'] and prev['MACD'] >= prev['SIGNAL'] else \
                         "➡️ 持续" if latest['MACD'] > latest['SIGNAL'] else "⬇️ 持续"

            result += f"MACD：{macd_signal}\n"

            result += "\n=== 成交量分析 ===\n"

            # 成交量分析
            avg_volume = df['成交量'].rolling(window=20).mean().iloc[-1]
            current_volume = latest['成交量']
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

            volume_sentiment = "🔥 放量" if volume_ratio > 1.5 else \
                              "📊 均量" if 0.8 <= volume_ratio <= 1.5 else \
                              "📉 缩量"

            result += f"成交量：{volume_sentiment} ({volume_ratio:.2f}倍)\n"

            result += "\n=== 综合技术情绪 ===\n"

            # 综合评分
            score = 0
            if latest['收盘'] > latest['MA20']:
                score += 2
            if 30 <= rsi_value <= 70:
                score += 1
            if latest['MACD'] > latest['SIGNAL']:
                score += 1
            if volume_ratio > 1:
                score += 1

            if score >= 4:
                overall_sentiment = "🟢 强势看多"
            elif score >= 2:
                overall_sentiment = "🟡 偏多"
            elif score >= 0:
                overall_sentiment = "🟠 偏空"
            else:
                overall_sentiment = "🔴 弱势"

            result += f"综合评分：{score}/5 分\n"
            result += f"技术情绪：{overall_sentiment}\n"

            result += "\n=== 操作建议 ===\n"
            if score >= 4:
                result += "• 技术形态强势，可考虑逢低建仓\n"
                result += "• 注意控制仓位，设置止损\n"
            elif score >= 2:
                result += "• 技术面偏多，谨慎看好\n"
                result += "• 建议结合基本面分析\n"
            elif score >= 0:
                result += "• 技术面偏空，观望为主\n"
                result += "• 等待更好的入场时机\n"
            else:
                result += "• 技术面弱势，建议规避\n"
                result += "• 如需操作，严格控制风险\n"

            return result

        except Exception as e:
            return f"技术情绪分析失败: {str(e)}"

    def _calculate_rsi(self, prices, period=14):
        """计算RSI指标"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi