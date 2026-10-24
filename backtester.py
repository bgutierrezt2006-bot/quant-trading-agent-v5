import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from risk_manager import RiskManager

load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"]

class Backtester:
    def __init__(self, api_key: str = None, secret_key: str = None, initial_capital: float = 100000.0):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.client = StockHistoricalDataClient(self.api_key, self.secret_key)
        self.initial_capital = initial_capital
        self.risk_mgr = RiskManager(max_risk_per_trade=0.01, rr_ratio=2.0, max_position_pct=0.25)

    def fetch_history(self, symbol: str, days: int = 365) -> pd.DataFrame:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=datetime.now() - timedelta(days=days + 80)
        )
        bars = self.client.get_stock_bars(request).df
        if bars.empty:
            return pd.DataFrame()

        df = bars.xs(symbol).copy() if isinstance(bars.index, pd.MultiIndex) else bars.copy()
        df["sma_10"] = df["close"].rolling(10).mean()
        df["sma_20"] = df["close"].rolling(20).mean()
        df["sma_50"] = df["close"].rolling(50).mean()

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

        return df.dropna().tail(252)

    def run_symbol_backtest(self, symbol: str, allocated_capital: float) -> dict:
        df = self.fetch_history(symbol, days=365)
        if df.empty:
            return {}

        equity = allocated_capital
        peak_equity = equity
        max_drawdown_pct = 0.0
        trades = []
        active_trade = None

        for idx, row in df.iterrows():
            open_p = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            sma_10 = float(row["sma_10"])
            sma_20 = float(row["sma_20"])
            sma_50 = float(row["sma_50"])
            rsi = float(row["rsi"])
            atr = float(row["atr"])

            if active_trade is not None:
                exit_price = None
                exit_reason = ""

                # Breakeven inteligente solo cuando ya alcanzó 1.4R de ganancia flotante
                be_trigger = active_trade["entry_price"] + (1.4 * active_trade["risk_per_share"])
                if high >= be_trigger and active_trade["stop_loss"] < active_trade["entry_price"]:
                    active_trade["stop_loss"] = round(active_trade["entry_price"] + (0.1 * active_trade["risk_per_share"]), 2)

                if low <= active_trade["stop_loss"]:
                    exit_price = active_trade["stop_loss"]
                    exit_reason = "Stop Loss / Profit Lock"
                elif high >= active_trade["take_profit"]:
                    exit_price = active_trade["take_profit"]
                    exit_reason = "Take Profit (2R)"
                elif rsi >= 76:
                    exit_price = close
                    exit_reason = "Toma Ganancia RSI > 76"

                if exit_price is not None:
                    pnl = (exit_price - active_trade["entry_price"]) * active_trade["qty"]
                    equity += pnl
                    peak_equity = max(peak_equity, equity)
                    dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                    max_drawdown_pct = max(max_drawdown_pct, dd)

                    trades.append({
                        "symbol": symbol,
                        "pnl": pnl,
                        "reason": exit_reason
                    })
                    active_trade = None
                continue

            not_overextended = (close - sma_20) <= (1.8 * atr)
            momentum_setup = (
                (close > sma_50)
                and (sma_20 > sma_50)
                and (sma_10 > sma_20)
                and (close > sma_20)
                and not_overextended
                and (50 <= rsi <= 64)
            )
            pullback_setup = (
                (close > sma_50)
                and (sma_20 > sma_50)
                and (close > open_p)
                and (36 <= rsi <= 50)
            )

            if momentum_setup or pullback_setup:
                plan = self.risk_mgr.build_order_plan(
                    symbol=symbol,
                    side="buy",
                    equity=equity,
                    buying_power=equity * 2,
                    current_price=close,
                    atr=atr
                )
                if plan and plan.qty > 0:
                    active_trade = {
                        "entry_price": plan.entry_price,
                        "stop_loss": plan.stop_loss,
                        "take_profit": plan.take_profit,
                        "risk_per_share": plan.entry_price - plan.stop_loss,
                        "qty": plan.qty
                    }

        if active_trade is not None:
            last_close = float(df.iloc[-1]["close"])
            pnl = (last_close - active_trade["entry_price"]) * active_trade["qty"]
            equity += pnl
            trades.append({"symbol": symbol, "pnl": pnl, "reason": "Fin de Periodo"})

        wins = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [abs(t["pnl"]) for t in trades if t["pnl"] <= 0]
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
        profit_factor = (sum(wins) / sum(losses)) if sum(losses) > 0 else (float("inf") if wins else 0.0)
        return_pct = ((equity - allocated_capital) / allocated_capital) * 100

        return {
            "symbol": symbol,
            "initial": allocated_capital,
            "final": equity,
            "net_pnl": equity - allocated_capital,
            "return_pct": return_pct,
            "trades": len(trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_dd_pct": max_drawdown_pct * 100
        }

    def run_portfolio(self):
        print("\n" + "="*80)
        print(" EVALUACIÓN DE SELECCIÓN DE ACTIVOS (UNIVERSO DE 6 ACCIONES - ÚLTIMOS 365 DÍAS)")
        print("="*80)
        per_symbol_cap = self.initial_capital / len(WATCHLIST)
        all_results = []

        for sym in WATCHLIST:
            res = self.run_symbol_backtest(sym, allocated_capital=per_symbol_cap)
            if res:
                all_results.append(res)
                pf_str = f"{res['profit_factor']:.2f}" if res["profit_factor"] != float("inf") else "INF"
                print(
                    f"[{res['symbol']:<5}] Trades: {res['trades']:<2} | WinRate: {res['win_rate']:>5.1f}% | "
                    f"PF: {pf_str:>5} | MaxDD: -{res['max_dd_pct']:>4.2f}% | "
                    f"PnL: ${res['net_pnl']:>+9,.2f} ({res['return_pct']:>+6.2f}%)"
                )

        # Ordenar por Profit Factor descendente
        sorted_results = sorted(all_results, key=lambda x: (x["profit_factor"] if x["profit_factor"] != float("inf") else 9999.0), reverse=True)
        top_4 = sorted_results[:4]
        top_4_symbols = [r["symbol"] for r in top_4]

        print("-" * 80)
        print(f"TOP 4 ACTIVOS SELECCIONADOS POR PROFIT FACTOR: {', '.join(top_4_symbols)}")
        print("-" * 80)

        # Simular portafolio enfocado en el Top 4 ($25k por símbolo = $100k)
        print("\n" + "="*80)
        print(" PORTAFOLIO FINAL OPTIMIZADO (TOP 4 ACTIVOS - $25,000 POR ACTIVO)")
        print("="*80)
        top4_cap = self.initial_capital / len(top_4)
        top4_final = 0.0
        top4_trades = 0

        for sym in top_4_symbols:
            res = self.run_symbol_backtest(sym, allocated_capital=top4_cap)
            if res:
                top4_final += res["final"]
                top4_trades += res["trades"]
                pf_str = f"{res['profit_factor']:.2f}" if res["profit_factor"] != float("inf") else "INF"
                print(
                    f"[{res['symbol']:<5}] Trades: {res['trades']:<2} | WinRate: {res['win_rate']:>5.1f}% | "
                    f"PF: {pf_str:>5} | MaxDD: -{res['max_dd_pct']:>4.2f}% | "
                    f"PnL: ${res['net_pnl']:>+9,.2f} ({res['return_pct']:>+6.2f}%)"
                )

        total_pnl = top4_final - self.initial_capital
        total_ret_pct = (total_pnl / self.initial_capital) * 100
        print("-" * 80)
        print(
            f"PORTAFOLIO TOP 4 -> Capital Inicial: ${self.initial_capital:,.2f} | "
            f"Capital Final: ${top4_final:,.2f} | PnL Neto: ${total_pnl:+,.2f} ({total_ret_pct:+.2f}%) | "
            f"Total Operaciones: {top4_trades}"
        )
        print("="*80 + "\n")

if __name__ == "__main__":
    bt = Backtester(API_KEY, SECRET_KEY, initial_capital=100000.0)
    bt.run_portfolio()
