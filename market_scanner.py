import os
from dotenv import load_dotenv
from backtester import Backtester
from logger import get_logger, logger

load_dotenv()
if logger is None:
    logger = get_logger()

class AutonomousMarketScanner:
    # Universo amplio de activos de alta liquidez en NYSE/Nasdaq
    UNIVERSE = [
        "MSFT", "AAPL", "GOOGL", "NVDA", "META", "AMZN",
        "AVGO", "AMD", "NFLX", "CRM", "COST", "JPM"
    ]

    def __init__(self, api_key: str = None, secret_key: str = None):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.bt = Backtester(self.api_key, self.secret_key, initial_capital=100000.0)

    def discover_best_markets(self, top_n: int = 4, min_pf: float = 1.25) -> list[str]:
        logger.info(f"[SCANNER AUTÓNOMO] Evaluando universo de {len(self.UNIVERSE)} activos con Backtesting en memoria...")
        candidates = []

        for symbol in self.UNIVERSE:
            try:
                res = self.bt.run_symbol_backtest(symbol, allocated_capital=25000.0)
                if not res or res["trades"] < 3:
                    continue
                pf = res["profit_factor"]
                wr = res["win_rate"]
                ret = res["return_pct"]
                logger.info(
                    f" -> [{symbol:<5}] Backtest 1Y | Trades: {res['trades']:<2} | "
                    f"WinRate: {wr:>5.1f}% | PF: {pf:>5.2f} | Retorno: {ret:>+6.2f}%"
                )
                if pf >= min_pf and ret > 0:
                    candidates.append(res)
            except Exception as e:
                logger.warning(f" -> [{symbol}] Omitido en escaneo: {e}")

        # Ordenar de mayor a menor Profit Factor
        candidates.sort(key=lambda x: x["profit_factor"], reverse=True)
        selected = [c["symbol"] for c in candidates[:top_n]]

        if not selected:
            selected = ["MSFT", "AAPL", "GOOGL", "AVGO"]

        logger.info(f"[SCANNER AUTÓNOMO] Mercados seleccionados autónomamente para operar hoy: {selected}")
        return selected

    def select_top_assets(self, top_n: int = 4) -> list[str]:
        """Alias compatible con el orquestador principal."""
        return self.discover_best_markets(top_n=top_n)

    def scan_market(self, top_n: int = 4) -> list[str]:
        """Alias compatible con el orquestador principal."""
        return self.discover_best_markets(top_n=top_n)

    def get_top_symbols(self, top_n: int = 4) -> list[str]:
        """Alias compatible con el orquestador principal."""
        return self.discover_best_markets(top_n=top_n)
