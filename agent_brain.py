from pydantic import BaseModel, Field

class TradeDecision(BaseModel):
    action: str = Field(description="BUY, HOLD, o CLOSE")
    confidence: float = Field(ge=0.0, le=1.0)
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    reason: str

class TradingAgentBrain:
    POSITIVE_TERMS = {
        "beat", "beats", "surge", "surges", "soar", "record", "upgrade", "upgrades",
        "bullish", "growth", "profit", "outperform", "buy", "strong", "rally",
        "partnership", "breakthrough", "gains", "raises", "optimism"
    }
    NEGATIVE_TERMS = {
        "miss", "misses", "plunge", "plunges", "drop", "downgrade", "downgrades",
        "bearish", "loss", "lawsuit", "investigation", "probe", "recall", "crash",
        "weak", "cuts", "warning", "slump", "decline", "fraud", "subpoena"
    }

    def analyze_news_sentiment(self, news_list: list[dict]) -> tuple[float, str]:
        if not news_list:
            return 0.0, "Sin noticias recientes"

        latest_headline = news_list[0].get("headline", "Sin titular")
        pos_hits, neg_hits = 0, 0

        for item in news_list:
            text = f"{item.get('headline', '')} {item.get('summary', '')}".lower()
            pos_hits += sum(1 for w in self.POSITIVE_TERMS if w in text)
            neg_hits += sum(1 for w in self.NEGATIVE_TERMS if w in text)

        total = pos_hits + neg_hits
        if total == 0:
            return 0.0, latest_headline

        raw_score = (pos_hits - neg_hits) / max(total, 3)
        return max(-1.0, min(1.0, round(raw_score, 2))), latest_headline

    def evaluate(self, features: dict, news_list: list[dict] | None = None,
                 external_intel: dict | None = None) -> TradeDecision:
        open_p = features.get("open", features["close"])
        close = features["close"]
        sma_10 = features["sma_10"]
        sma_20 = features["sma_20"]
        sma_50 = features["sma_50"]
        rsi = features["rsi"]
        atr = features["atr"]

        broker_sentiment, top_headline = self.analyze_news_sentiment(news_list or [])
        ext = external_intel or {}
        social_score = ext.get("social_score", 0.0)
        upside_pct = ext.get("analyst_upside_pct", 0.0)
        earnings_soon = ext.get("earnings_soon", False)

        # Fusión de sentimiento: 50% Noticias Broker + 50% Pulso Externo (X/Stocktwits/Reddit/Google News)
        combined_sentiment = round((broker_sentiment * 0.5) + (social_score * 0.5), 2)

        # 1. Veto estricto si la empresa reporta ganancias en <= 2 días (evita gaps de lotería)
        if earnings_soon:
            return TradeDecision(
                action="HOLD",
                confidence=0.90,
                sentiment_score=combined_sentiment,
                reason=f"VETO POR EARNINGS INMINENTES (en {ext.get('days_to_earnings')} días). Evitando riesgo de gap."
            )

        not_overextended = (close - sma_20) <= (1.8 * atr)
        momentum_setup = (
            (close > sma_50) and (sma_20 > sma_50) and (sma_10 > sma_20)
            and (close > sma_20) and not_overextended and (50 <= rsi <= 64)
        )
        pullback_setup = (
            (close > sma_50) and (sma_20 > sma_50)
            and (close > open_p) and (36 <= rsi <= 50)
        )

        if momentum_setup or pullback_setup:
            setup_name = "Momentum Controlado" if momentum_setup else "Pullback Institucional"

            if combined_sentiment <= -0.25:
                return TradeDecision(
                    action="HOLD",
                    confidence=0.70,
                    sentiment_score=combined_sentiment,
                    reason=(
                        f"VETO MULTI-PLATAFORMA en {setup_name}: Sentimiento combinado negativo ({combined_sentiment:+.2f} | "
                        f"Redes/X: {social_score:+.2f})."
                    )
                )

            # Bonus de confianza si los analistas de Wall Street ven potencial alcista (Upside > 5%)
            wall_street_bonus = 0.05 if upside_pct >= 5.0 else 0.0
            adjusted_confidence = min(0.95, round(0.80 + (combined_sentiment * 0.15) + wall_street_bonus, 2))

            return TradeDecision(
                action="BUY",
                confidence=adjusted_confidence,
                sentiment_score=combined_sentiment,
                reason=(
                    f"Setup {setup_name} | Sentimiento Global: {combined_sentiment:+.2f} "
                    f"(Broker: {broker_sentiment:+.2f}, Redes/X: {social_score:+.2f}) | "
                    f"Upside WallSt: {upside_pct:+.1f}%"
                )
            )

        return TradeDecision(
            action="HOLD",
            confidence=0.50,
            sentiment_score=combined_sentiment,
            reason=(
                f"Sin setup técnico (Close: ${close:.2f}, SMA50:${sma_50:.2f}, RSI: {rsi:.1f}) | "
                f"Sentimiento Redes/X: {social_score:+.2f} | Upside Analistas: {upside_pct:+.1f}%"
            )
        )

    def evaluate_opportunity(self, symbol: str, metrics: dict, news: list[dict] | None = None,
                             external_intel: dict | None = None) -> TradeDecision:
        """Alias compatible con RealTimeTradingEngine."""
        return self.evaluate(features=metrics, news_list=news, external_intel=external_intel)

AgentBrain = TradingAgentBrain
