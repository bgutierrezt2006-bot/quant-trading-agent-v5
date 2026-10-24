import os
import time
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
    GetOrdersRequest,
    ReplaceOrderRequest
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderClass,
    QueryOrderStatus
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from risk_manager import OrderPlan
from logger import get_logger, logger

if logger is None:
    logger = get_logger()


class CircuitBreakerResult(tuple):
    """Tupla que se evalúa a True o False en contextos booleanos."""
    def __new__(cls, tripped: bool, status: dict):
        return super().__new__(cls, (tripped, status))

    def __bool__(self):
        return bool(self[0])


class AlpacaExecutor:
    def __init__(self, api_key: str = None, secret_key: str = None, paper: bool = True):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.client = TradingClient(self.api_key, self.secret_key, paper=paper)
        self.data_client = StockHistoricalDataClient(self.api_key, self.secret_key)

    def is_market_open(self) -> bool:
        clock = self.client.get_clock()
        return clock.is_open

    def get_account_info(self):
        return self.client.get_account()

    def get_account_status(self) -> dict:
        account = self.client.get_account()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        buying_power = float(account.buying_power)
        daily_pnl = equity - last_equity
        daily_pnl_pct = (daily_pnl / last_equity) if last_equity > 0 else 0.0

        return {
            "equity": equity,
            "last_equity": last_equity,
            "buying_power": buying_power,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct
        }

    def check_circuit_breaker(self, max_daily_loss_pct: float = 0.02) -> CircuitBreakerResult:
        """
        Retorna (True, status) si se disparó el freno de emergencia (pérdida diaria >= max_daily_loss_pct).
        Evalúa directamente como booleano en `if check_circuit_breaker():`
        y permite desempaquetado `halted, status = check_circuit_breaker()`.
        """
        status = self.get_account_status()
        if status["daily_pnl_pct"] <= -abs(max_daily_loss_pct):
            logger.critical(
                f"[CIRCUIT BREAKER ACTIVADO] Pérdida diaria de {status['daily_pnl_pct']*100:.2f}% "
                f"(${status['daily_pnl']:,.2f}) supera el límite de -{max_daily_loss_pct*100:.2f}%. "
                "Cancelando órdenes abiertas y bloqueando nuevas entradas por hoy."
            )
            self.client.cancel_orders()
            return CircuitBreakerResult(True, status)
        return CircuitBreakerResult(False, status)

    def get_open_positions(self):
        """Retorna la lista de posiciones abiertas actualmente."""
        return self.client.get_all_positions()

    def get_all_positions(self):
        return self.get_open_positions()

    def has_open_position_or_order(self, symbol: str) -> bool:
        positions = self.client.get_all_positions()
        if any(p.symbol == symbol for p in positions):
            return True
        orders = self.client.get_orders()
        if any(o.symbol == symbol for o in orders):
            return True
        return False

    def close_position_safely(self, symbol: str) -> bool:
        """
        Cancela primero las órdenes abiertas (SL/TP del Bracket) del símbolo
        para liberar las acciones y luego liquida la posición a mercado inmediatamente.
        """
        try:
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            open_orders = self.client.get_orders(filter=req)
            for order in open_orders:
                self.client.cancel_order_by_id(order.id)
                logger.info(f"🔓 Orden abierta {order.id} de {symbol} cancelada para liberar acciones.")

            if open_orders:
                time.sleep(0.4)

            self.client.close_position(symbol)
            logger.info(f"✅ Posición en {symbol} cerrada exitosamente en tiempo real.")
            return True
        except Exception as e:
            logger.error(f"❌ Error al cerrar posición de {symbol} en vivo: {e}")
            return False

    def update_stop_loss_on_server(self, symbol: str, new_stop_price: float) -> bool:
        """
        Modifica físicamente la orden Stop Loss abierta en los servidores de Alpaca
        para blindar el Profit Lock (+0.1R) aunque se apague el bot.
        """
        try:
            new_stop = round(float(new_stop_price), 2)
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            open_orders = self.client.get_orders(filter=req)

            for order in open_orders:
                order_type_str = str(getattr(order, "order_type", getattr(order, "type", ""))).lower()
                if getattr(order, "stop_price", None) is not None or "stop" in order_type_str:
                    current_stop = float(order.stop_price) if order.stop_price else 0.0
                    if new_stop > current_stop:
                        replace_req = ReplaceOrderRequest(stop_price=new_stop)
                        self.client.replace_order_by_id(order.id, replace_req)
                        logger.info(
                            f"🛡️ [SERVER SL UPDATED] Orden Stop Loss de {symbol} ({order.id}) "
                            f"subida en el servidor de Alpaca: ${current_stop:.2f} -> ${new_stop:.2f}"
                        )
                    return True

            logger.warning(f"⚠️ No se encontró orden Stop Loss abierta en Alpaca para {symbol}. Manteniendo vigilancia en RAM/Disco.")
            return False
        except Exception as e:
            logger.warning(f"⚠️ Aviso al reemplazar Stop Loss en servidor para {symbol}: {e}. Vigilancia activa por software.")
            return False

    def check_spread_is_safe(self, symbol: str, max_spread_pct: float = 0.0015) -> bool:
        """
        Verifica que el spread Bid-Ask no supere el 0.15% antes de enviar una orden a mercado,
        evitando pérdidas por slippage en momentos de baja liquidez.
        """
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote_map = self.data_client.get_stock_latest_quote(req)
            quote = quote_map.get(symbol) if isinstance(quote_map, dict) else quote_map

            if quote and getattr(quote, "ask_price", 0) > 0 and getattr(quote, "bid_price", 0) > 0:
                ask = float(quote.ask_price)
                bid = float(quote.bid_price)
                if ask >= bid:
                    spread_pct = (ask - bid) / ask
                    if spread_pct > max_spread_pct:
                        logger.warning(
                            f"⚠️ [SLIPPAGE GUARD] Spread alto en {symbol}: {spread_pct*100:.3f}% "
                            f"(Bid: ${bid:.2f} / Ask: ${ask:.2f} > máx {max_spread_pct*100:.2f}%). Compra pospuesta."
                        )
                        return False
            return True
        except Exception as e:
            logger.debug(f"No se pudo verificar quote en vivo para {symbol}: {e}")
            return True

    def manage_open_positions(self, symbol: str, features: dict) -> bool:
        """
        Revisa si ya tenemos una posición abierta en ese símbolo y, en caso de que
        el RSI >= 76 (sobrecompra extrema) o close < sma_50 (ruptura estructural),
        envía el cierre de protección de esa posición y cancela sus órdenes hijas pendientes.
        Retorna True si se ejecutó el cierre de protección, False en caso contrario.
        """
        positions = self.client.get_all_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if not pos:
            logger.info(f"[{symbol}] Orden pendiente activa sin posición ejecutada. Saltando gestión activa.")
            return False

        rsi = features.get("rsi")
        close = features.get("close")
        sma_50 = features.get("sma_50")

        should_close = False
        reasons = []

        if rsi is not None and rsi >= 76:
            should_close = True
            reasons.append(f"RSI en sobrecompra extrema ({rsi:.1f} >= 76)")

        if close is not None and sma_50 is not None and close < sma_50:
            should_close = True
            reasons.append(f"Ruptura estructural (Close ${close:.2f} < SMA50 ${sma_50:.2f})")

        if should_close:
            reason_str = " y ".join(reasons)
            logger.warning(
                f"[GESTIÓN ACTIVA - {symbol}] Disparada salida de protección por {reason_str}. "
                f"Liquidando posición de forma segura..."
            )
            return self.close_position_safely(symbol)
        else:
            logger.info(
                f"[GESTIÓN ACTIVA - {symbol}] Posición abierta en seguimiento | Qty: {pos.qty} | "
                f"PnL no realizado: ${float(pos.unrealized_pl):+,.2f} ({float(pos.unrealized_plpc)*100:+.2f}%) | "
                f"Close: ${close:.2f} (SMA50: ${sma_50:.2f}) | RSI: {rsi:.1f} (Umbral: 76) -> Sin gatillo de salida."
            )
            return False

    def execute_bracket_order(self, plan: OrderPlan):
        order_data = MarketOrderRequest(
            symbol=plan.symbol,
            qty=plan.qty,
            side=OrderSide.BUY if plan.side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=plan.take_profit),
            stop_loss=StopLossRequest(stop_price=plan.stop_loss)
        )
        order = self.client.submit_order(order_data)
        logger.info(
            f"[EJECUCIÓN] Bracket Order enviada para {plan.symbol} | ID: {order.id} | "
            f"Qty: {plan.qty} | Entrada Est.: ${plan.entry_price:.2f} | "
            f"SL: ${plan.stop_loss:.2f} | TP: ${plan.take_profit:.2f} | Riesgo: ${plan.risk_amount:.2f}"
        )
        return order
