import requests
import feedparser
import yfinance as yf
from datetime import datetime
from urllib.parse import quote_plus

class ExternalWebResearcher:
    POSITIVE_WORDS = {
        "beat", "bullish", "surge", "soar", "upgrade", "buy", "strong", "record",
        "outperform", "breakout", "moon", "calls", "rally", "growth", "profit", "optimism"
    }
    NEGATIVE_WORDS = {
        "miss", "bearish", "plunge", "crash", "downgrade", "sell", "weak", "lawsuit",
        "puts", "dump", "drop", "warning", "investigation", "fraud", "cut", "overvalued"
    }

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuantResearchAgent/4.0"
        }

    def _score_texts(self, texts: list[str]) -> float:
        if not texts:
            return 0.0
        pos, neg = 0, 0
        for t in texts:
            low = t.lower()
            pos += sum(1 for w in self.POSITIVE_WORDS if w in low)
            neg += sum(1 for w in self.NEGATIVE_WORDS if w in low)
        total = pos + neg
        if total == 0:
            return 0.0
        return max(-1.0, min(1.0, round((pos - neg) / max(total, 3), 2)))

    def get_yahoo_fundamentals(self, symbol: str) -> dict:
        """Extrae consenso de Wall Street y proximidad de Earnings desde Yahoo Finance."""
        result = {
            "analyst_upside_pct": 0.0,
            "recommendation": "none",
            "earnings_soon": False,
            "days_to_earnings": None
        }
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            curr = info.get("currentPrice") or info.get("regularMarketPrice")
            target = info.get("targetMeanPrice")
            if curr and target and curr > 0:
                result["analyst_upside_pct"] = round(((target - curr) / curr) * 100, 2)
            result["recommendation"] = info.get("recommendationKey", "none")

            cal = ticker.calendar
            if isinstance(cal, dict) and "Earnings Date" in cal:
                dates = cal["Earnings Date"]
                if dates:
                    next_date = dates[0]
                    target_date = next_date.date() if hasattr(next_date, "date") and callable(next_date.date) else next_date
                    if hasattr(target_date, "strftime"):
                        delta_days = (target_date - datetime.now().date()).days
                        result["days_to_earnings"] = delta_days
                        if 0 <= delta_days <= 2:
                            result["earnings_soon"] = True
        except Exception:
            pass
        return result

    def get_x_and_social_pulse(self, symbol: str) -> dict:
        """
        Extrae pulso social de traders desde:
        1. Búsqueda indexada de X/Twitter y Google News sobre el Cashtag ($SYMBOL)
        2. Stocktwits API (stream de cashtags en vivo)
        3. Reddit JSON API (r/wallstreetbets + r/stocks)
        """
        collected_texts = []
        bullish_tags = 0
        bearish_tags = 0

        # 1. Stocktwits Cashtag Stream ($SYMBOL)
        try:
            st_url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
            r = requests.get(st_url, headers=self.headers, timeout=5)
            if r.status_code == 200:
                msgs = r.json().get("messages", [])[:15]
                for m in msgs:
                    body = m.get("body", "")
                    if body:
                        collected_texts.append(body)
                    ent = m.get("entities", {}).get("sentiment", {})
                    if ent:
                        if ent.get("basic") == "Bullish":
                            bullish_tags += 1
                        elif ent.get("basic") == "Bearish":
                            bearish_tags += 1
        except Exception:
            pass

        # 2. Reddit API (r/wallstreetbets & r/stocks menciones recientes)
        try:
            rd_url = f"https://www.reddit.com/search.json?q=%24{symbol}+OR+{symbol}+stock&sort=new&t=day&limit=8"
            r = requests.get(rd_url, headers=self.headers, timeout=5)
            if r.status_code == 200:
                posts = r.json().get("data", {}).get("children", [])
                for p in posts:
                    title = p.get("data", {}).get("title", "")
                    if title:
                        collected_texts.append(title)
        except Exception:
            pass

        # 3. Google News + Indexación Web/X para el Cashtag
        try:
            query = quote_plus(f"${symbol} OR {symbol} stock when:1d")
            rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:8]:
                collected_texts.append(entry.title)
        except Exception:
            pass

        text_score = self._score_texts(collected_texts)

        # Combinar etiquetas explícitas de traders (Bullish/Bearish) con el análisis de texto
        tag_total = bullish_tags + bearish_tags
        if tag_total > 0:
            tag_score = (bullish_tags - bearish_tags) / tag_total
            final_social_score = round((text_score * 0.5) + (tag_score * 0.5), 2)
        else:
            final_social_score = text_score

        sample_post = collected_texts[0][:80] if collected_texts else "Sin menciones recientes"
        return {
            "social_score": max(-1.0, min(1.0, final_social_score)),
            "mentions_analyzed": len(collected_texts),
            "bullish_traders": bullish_tags,
            "bearish_traders": bearish_tags,
            "top_mention": sample_post
        }

    def investigate_symbol(self, symbol: str) -> dict:
        fundamentals = self.get_yahoo_fundamentals(symbol)
        social = self.get_x_and_social_pulse(symbol)
        return {
            "symbol": symbol,
            **fundamentals,
            **social
        }

    def research_symbol(self, symbol: str) -> dict:
        """Alias compatible con RealTimeTradingEngine."""
        return self.investigate_symbol(symbol)
