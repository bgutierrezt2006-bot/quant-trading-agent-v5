import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient, NewsClient
from alpaca.data.requests import StockBarsRequest, NewsRequest
from alpaca.data.timeframe import TimeFrame

load_dotenv()

class MarketDataProvider:
    def __init__(self, api_key: str = None, secret_key: str = None):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.client = StockHistoricalDataClient(self.api_key, self.secret_key)
        self.news_client = NewsClient(self.api_key, self.secret_key)

    def get_technical_features(self, symbol: str, lookback_days: int = 100) -> dict | None:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=datetime.now() - timedelta(days=lookback_days)
        )
        bars = self.client.get_stock_bars(request).df
        if bars.empty or len(bars) < 55:
            return None

        df = bars.xs(symbol) if isinstance(bars.index, pd.MultiIndex) else bars.copy()

        df["sma_10"] = df["close"].rolling(10).mean()
        df["sma_20"] = df["close"].rolling(20).mean()
        df["sma_50"] = df["close"].rolling(50).mean()
        df["vol_sma_20"] = df["volume"].rolling(20).mean()

        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = tr.rolling(14).mean()

        latest = df.iloc[-1]
        return {
            "symbol": symbol,
            "open": float(latest["open"]),
            "close": float(latest["close"]),
            "sma_10": float(latest["sma_10"]),
            "sma_20": float(latest["sma_20"]),
            "sma_50": float(latest["sma_50"]),
            "rsi": float(latest["rsi"]),
            "atr": float(latest["atr"]),
            "volume": float(latest["volume"]),
            "vol_sma_20": float(latest["vol_sma_20"])
        }

    def get_latest_metrics(self, symbol: str) -> dict | None:
        """Alias para compatibilidad con RealTimeTradingEngine."""
        return self.get_technical_features(symbol)

    def get_recent_news(self, symbol: str, limit: int = 5) -> list[dict]:
        try:
            request = NewsRequest(symbols=symbol, limit=limit)
            response = self.news_client.get_news(request)
            news_items = response.data.get("news", []) if hasattr(response, "data") else []
            results = []
            for item in news_items:
                results.append({
                    "headline": getattr(item, "headline", "") if not isinstance(item, dict) else item.get("headline", ""),
                    "summary": getattr(item, "summary", "") if not isinstance(item, dict) else item.get("summary", ""),
                    "created_at": str(getattr(item, "created_at", "") if not isinstance(item, dict) else item.get("created_at", ""))
                })
            return results
        except Exception:
            return []
