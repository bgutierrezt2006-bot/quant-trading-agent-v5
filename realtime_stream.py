import os
import time
import asyncio
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed
from alpaca.trading.stream import TradingStream

from logger import logger
from data_provider import MarketDataProvider
from web_researcher import ExternalWebResearcher
from agent_brain import AgentBrain
from risk_manager import RiskManager
from execution import AlpacaExecutor
from state_manager import StateManager
from notifier import DiscordNotifier

load_dotenv()

BENCHMARK_SYMBOL = "SPY"


class RealTimeTradingEngine:
    def __init__(self, active_watchlist: list[str]):
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY")
        self.watchlist = list(active_watchlist)

        self.data_provider = MarketDataProvider()
        self.web_researcher = ExternalWebResearcher()
        self.brain = AgentBrain()
        self.risk_manager = RiskManager()
        self.executor = AlpacaExecutor()
        self.state_manager = StateManager()
        self.notifier = DiscordNotifier()

        # Cachés de doble velocidad y control de estado persistente
        self.indicator_cache = {}
        self.research_cache = {}
        self.open_positions_cache = {}
        self.entry_cooldown = {}
        self.last_trade_eval_ts = {}
        self.closing_in_progress = set()
        self.is_running = False

        self.stock_stream = None
        self.trading_stream = None

    def update_watchlist(self, new_watchlist: list[str]):
        self.watchlist = list(new_watchlist)

    def refresh_positions_cache(self):
        """Sincroniza en memoria y con bot_state.json las posiciones abiertas en Alpaca."""
        try:
            positions = self.executor.get_open_positions()
            self.open_positions_cache = {p.symbol: p for p in positions}
            active_syms = set(self.open_positions_cache.keys())
            self.closing_in_progress.intersection_update(active_syms)
            self.state_manager.sync_with_open_positions(active_syms)
        except Exception as e:
            logger.error(f"Error sincronizando posiciones abiertas: {e}")

    def refresh_context_cache(self):
        """
        Actualiza la capa estructural (indicadores diarios + SPY macro + scraping web/X/noticias).
        Se ejecuta cada 10 minutos en segundo plano sin bloquear el WebSocket.
        """
        self.refresh_positions_cache()
        all_symbols = list(set(self.watchlist + list(self.open_positions_cache.keys()) + [BENCHMARK_SYMBOL]))
        logger.info(f"🔄 [CACHE REFRESH V5.5] Actualizando estructura, Macro ({BENCHMARK_SYMBOL}) y Web para: {all_symbols}")

        for symbol in all_symbols:
            try:
                metrics = self.data_provider.get_latest_metrics(symbol)
                if metrics:
                    prev_close = float(metrics.get("close", 0.0))
                    news = self.data_provider.get_recent_news(symbol) if symbol != BENCHMARK_SYMBOL else []
                    self.indicator_cache[symbol] = {
                        "metrics": metrics,
                        "daily_ref_close": prev_close,
                        "news": news,
                        "updated_at": time.time()
                    }

                if symbol != BENCHMARK_SYMBOL:
                    web_intel = self.web_researcher.research_symbol(symbol)
                    if web_intel:
                        self.research_cache[symbol] = web_intel
            except Exception as e:
                logger.error(f"Error actualizando caché de contexto para {symbol}: {e}")

    def _calculate_live_metrics(self, symbol: str, live_price: float, live_open: float = None) -> dict:
        """
        Proyecta los indicadores técnicos en tiempo real combinando el precio actual
        del WebSocket con la estructura diaria cacheada en RAM.
        """
        cache_entry = self.indicator_cache[symbol]
        base = cache_entry["metrics"].copy()
        ref_close = float(cache_entry.get("daily_ref_close", base.get("close", live_price)))

        price_diff_pct = (live_price - ref_close) / ref_close if ref_close else 0.0
        rsi_shift = (price_diff_pct * 100.0) * 2.2
        live_rsi = max(5.0, min(95.0, float(base.get("rsi", 50.0)) + rsi_shift))

        base["close"] = float(live_price)
        if live_open is not None:
            base["open"] = float(base.get("open", live_open))
        base["rsi"] = round(live_rsi, 2)
        base["intraday_change_pct"] = round(price_diff_pct * 100.0, 2)
        return base

    def is_opening_noise_window(self) -> bool:
        """
        Detecta si estamos en los primeros 15 minutos de apertura de Wall Street (09:30 a 09:45 NY),
        donde los spreads se abren y hay barridos de liquidez institucional.
        """
        now_ny = datetime.now(ZoneInfo("America/New_York")).time()
        return dtime(9, 30) <= now_ny < dtime(9, 45)

    def is_macro_regime_healthy(self) -> tuple[bool, str]:
        """
        Escudo Macro V5.5: Verifica la salud del índice S&P 500 (SPY).
        Bloquea nuevas compras si SPY sufre una liquidación intradía (<= -1.0%)
        o si cotiza bajo su SMA 50 con momentum débil (RSI < 45).
        """
        if BENCHMARK_SYMBOL not in self.indicator_cache:
            return True, "SPY OK (Sin datos previos)"

        spy_entry = self.indicator_cache[BENCHMARK_SYMBOL]
        spy_metrics = spy_entry["metrics"]
        spy_close = float(spy_metrics.get("close", 0.0))
        spy_sma50 = float(spy_metrics.get("sma_50", 0.0))
        spy_rsi = float(spy_metrics.get("rsi", 50.0))
        ref_close = float(spy_entry.get("daily_ref_close", spy_close))

        intraday_pct = ((spy_close - ref_close) / ref_close * 100.0) if ref_close else 0.0

        if intraday_pct <= -1.0:
            return False, f"Escudo Macro Activo: SPY cayendo fuerte intradía ({intraday_pct:.2f}% <= -1.0%)"

        if spy_close < spy_sma50 and spy_rsi < 45.0:
            return False, f"Escudo Macro Activo: SPY en régimen bajista (Close ${spy_close:.2f} < SMA50 ${spy_sma50:.2f}, RSI {spy_rsi:.1f})"

        return True, f"SPY Saludable (${spy_close:.2f} | RSI {spy_rsi:.1f} | Intradía {intraday_pct:+.2f}%)"

    async def on_minute_bar(self, bar):
        """
        Callback que se dispara automáticamente al cierre de cada vela de 1 minuto en el WebSocket.
        Actualiza el barómetro SPY, gestiona posiciones abiertas y evalúa entradas filtradas.
        """
        symbol = bar.symbol
        live_price = float(bar.close)
        live_open = float(bar.open)

        if symbol not in self.indicator_cache:
            return

        live_metrics = self._calculate_live_metrics(symbol, live_price, live_open)

        # Si es el índice SPY, actualizamos su estado macro en vivo y terminamos
        if symbol == BENCHMARK_SYMBOL:
            self.indicator_cache[BENCHMARK_SYMBOL]["metrics"]["close"] = live_price
            self.indicator_cache[BENCHMARK_SYMBOL]["metrics"]["rsi"] = live_metrics["rsi"]
            return

        if self.executor.check_circuit_breaker():
            logger.warning("🚨 Circuit Breaker Diario activado (-2.0% DD). Deteniendo motor en tiempo real.")
            self.notifier.send_embed(
                title="🚨 CIRCUIT BREAKER ACTIVADO",
                description="El bot detuvo la sesión en vivo por límite de pérdida diaria (-2.0%).",
                color=0xE74C3C
            )
            await self.stop()
            return

        # 1. Si ya tenemos posición abierta en este activo, la gestionamos siempre (incluso entre 9:30 y 9:45)
        if symbol in self.open_positions_cache:
            await self._manage_position_realtime(symbol, self.open_positions_cache[symbol], live_metrics)
            return

        # 2. Filtro de Ruido de Apertura (09:30 - 09:45 AM NY)
        if self.is_opening_noise_window():
            logger.info(f"⏳ [OPENING GUARD 9:30-9:45 NY] Posponiendo evaluación de compra en {symbol} para evitar ruido de apertura.")
            return

        # 3. Escudo de Régimen Macro (SPY)
        macro_ok, macro_reason = self.is_macro_regime_healthy()
        if not macro_ok:
            logger.warning(f"🛡️ [MACRO VETO] Compra bloqueada en {symbol} -> {macro_reason}")
            return

        # 4. Cooldown de 5 minutos por símbolo tras intento de compra
        if time.time() - self.entry_cooldown.get(symbol, 0) < 300:
            return

        news = self.indicator_cache[symbol].get("news", [])
        web_intel = self.research_cache.get(symbol, {})

        decision = self.brain.evaluate_opportunity(
            symbol=symbol,
            metrics=live_metrics,
            news=news,
            external_intel=web_intel
        )

        logger.info(
            f"⚡ [BAR 1M {symbol}] Precio: ${live_price:.2f} | RSI Live: {live_metrics['rsi']} | "
            f"Acción: {decision.action} (Conf: {decision.confidence:.2f}) | {decision.reason}"
        )

        if decision.action == "BUY" and decision.confidence >= 0.75:
            self.entry_cooldown[symbol] = time.time()
            await asyncio.to_thread(
                self._execute_realtime_buy,
                symbol,
                live_price,
                live_metrics["atr"],
                decision.confidence,
                decision.reason
            )

    def _execute_realtime_buy(self, symbol: str, live_price: float, atr: float, confidence: float = 0.80, reason: str = ""):
        """Verifica el spread Bid-Ask, calcula el plan de riesgo y envía la orden Bracket."""
        try:
            if not self.executor.check_spread_is_safe(symbol, max_spread_pct=0.0015):
                return

            account = self.executor.get_account_info()
            order_plan = self.risk_manager.calculate_order_plan(
                symbol=symbol,
                entry_price=live_price,
                atr=atr,
                account_equity=float(account.equity),
                buying_power=float(account.buying_power)
            )
            if order_plan and order_plan.qty > 0:
                logger.info(f"🎯 [REALTIME BUY V5.5] Enviando orden Bracket para {symbol}: {order_plan}")
                self.executor.execute_bracket_order(order_plan)
                self.notifier.notify_buy_order(order_plan, confidence, reason)
                self.refresh_positions_cache()
        except Exception as e:
            logger.error(f"Error ejecutando compra en tiempo real para {symbol}: {e}")

    async def on_realtime_trade(self, trade):
        """
        Escucha cada tick/trade en tiempo real (con throttling de 2 seg por activo)
        exclusivamente para proteger posiciones abiertas (Profit Lock 1.4R persistente, RSI >= 76, SMA50).
        """
        symbol = trade.symbol
        if symbol not in self.open_positions_cache or symbol not in self.indicator_cache:
            return

        now = time.time()
        if now - self.last_trade_eval_ts.get(symbol, 0) < 2.0:
            return
        self.last_trade_eval_ts[symbol] = now

        live_price = float(trade.price)
        live_metrics = self._calculate_live_metrics(symbol, live_price)
        await self._manage_position_realtime(symbol, self.open_positions_cache[symbol], live_metrics)

    async def _manage_position_realtime(self, symbol: str, position, live_metrics: dict):
        """
        Gestión activa sub-segundo de posiciones abiertas con Persistencia en Disco, SL en Servidor y Alertas Discord:
        1. Toma de ganancias por sobrecompra extrema (RSI >= 76).
        2. Cierre defensivo por pérdida de soporte estructural (Close < SMA 50).
        3. Profit Lock Dinámico 1.4R: sube físicamente el Stop Loss en Alpaca a Entry + 0.1R y lo guarda en bot_state.json.
        """
        if symbol in self.closing_in_progress:
            return

        live_price = live_metrics["close"]
        rsi = live_metrics["rsi"]
        sma_50 = live_metrics["sma_50"]
        atr = live_metrics["atr"]
        entry_price = float(position.avg_entry_price)

        # 1. Toma de ganancia por RSI extremo
        if rsi >= 76.0:
            self.closing_in_progress.add(symbol)
            logger.info(f"💰 [LIVE EXIT - RSI] {symbol} en ${live_price:.2f} alcanzó RSI {rsi} (>=76). Cerrando posición.")
            await asyncio.to_thread(self.executor.close_position_safely, symbol)
            self.state_manager.remove_profit_lock(symbol)
            self.notifier.notify_position_closed(
                symbol, live_price, f"Toma de ganancias por sobrecompra extrema (RSI {rsi:.1f} >= 76)"
            )
            await asyncio.to_thread(self.refresh_positions_cache)
            return

        # 2. Protección estructural por SMA 50
        if live_price < sma_50:
            self.closing_in_progress.add(symbol)
            logger.warning(f"🛡️ [LIVE EXIT - SMA50] {symbol} (${live_price:.2f}) perdió SMA50 (${sma_50:.2f}). Cerrando posición.")
            await asyncio.to_thread(self.executor.close_position_safely, symbol)
            self.state_manager.remove_profit_lock(symbol)
            self.notifier.notify_position_closed(
                symbol, live_price, f"Ruptura defensiva de SMA 50 (${live_price:.2f} < ${sma_50:.2f})"
            )
            await asyncio.to_thread(self.refresh_positions_cache)
            return

        # 3. Profit Lock Dinámico Persistente (1.4R -> Protege en Entry + 0.1R en Servidor y en Disco)
        risk_1r = 2.0 * atr
        if risk_1r > 0:
            current_r = (live_price - entry_price) / risk_1r
            existing_lock = self.state_manager.get_profit_lock(symbol)

            if current_r >= 1.4 and not existing_lock:
                lock_price = round(entry_price + (0.1 * risk_1r), 2)
                logger.info(
                    f"🔒 [PROFIT LOCK 1.4R ACTIVADO] {symbol} alcanzó +{current_r:.2f}R (${live_price:.2f}). "
                    f"Subiendo Stop Loss a ${lock_price:.2f} (+0.1R) en servidor Alpaca y bot_state.json."
                )
                server_ok = await asyncio.to_thread(
                    self.executor.update_stop_loss_on_server, symbol, lock_price
                )
                self.state_manager.set_profit_lock(
                    symbol=symbol,
                    lock_price=lock_price,
                    entry_price=entry_price,
                    server_updated=server_ok
                )
                self.notifier.notify_profit_lock(symbol, current_r, live_price, lock_price, server_ok)
                existing_lock = self.state_manager.get_profit_lock(symbol)

            # Respaldo en vivo por software por si el precio cae a lock_price antes del fill del servidor
            if existing_lock:
                lock_price = float(existing_lock["lock_price"])
                if live_price <= lock_price:
                    self.closing_in_progress.add(symbol)
                    logger.info(
                        f"🛡️ [PROFIT LOCK HIT] {symbol} retrocedió a ${live_price:.2f} (<= ${lock_price:.2f}). "
                        f"Asegurando ganancia protegida (+0.1R)."
                    )
                    await asyncio.to_thread(self.executor.close_position_safely, symbol)
                    self.state_manager.remove_profit_lock(symbol)
                    self.notifier.notify_position_closed(
                        symbol, live_price, f"Profit Lock Hit en ${lock_price:.2f} (+0.1R asegurado)"
                    )
                    await asyncio.to_thread(self.refresh_positions_cache)

    async def on_trade_update(self, data):
        """Escucha eventos de órdenes del broker en tiempo real (fills, stops, cancelaciones)."""
        event = data.event
        order = data.order
        if event in ("fill", "partial_fill", "canceled", "rejected"):
            logger.info(
                f"🔔 [BROKER STREAM] Evento: {event.upper()} | {order.symbol} | "
                f"Side: {order.side} | Qty: {order.filled_qty}/{order.qty} | AvgPrice: {order.filled_avg_price}"
            )
            if event == "fill":
                self.notifier.send_embed(
                    title=f"🔔 ORDEN EJECUTADA (FILL): {order.symbol}",
                    description=(
                        f"La orden de **{order.side}** para **{order.symbol}** se completó en Alpaca "
                        f"a un precio promedio de `${order.filled_avg_price}` ({order.filled_qty}/{order.qty} acciones)."
                    ),
                    color=0x2ECC71
                )
            await asyncio.to_thread(self.refresh_positions_cache)

    async def _background_maintenance_loop(self):
        """
        Refresca la caché pesada cada 10 minutos y detiene limpiamente el stream
        cuando cierra el mercado de Nueva York.
        """
        while self.is_running:
            await asyncio.sleep(60)

            # Límite de tiempo si se ejecuta en Cloud Runner (ej. GitHub Actions)
            max_runtime = os.getenv("MAX_RUNTIME_MINUTES")
            if max_runtime:
                try:
                    limit_sec = float(max_runtime) * 60.0
                    if time.time() - getattr(self, "session_start_ts", time.time()) >= limit_sec:
                        logger.info("⏰ Límite de sesión alcanzado en bucle WebSocket. Deteniendo WebSockets limpiamente...")
                        await self.stop()
                        break
                except ValueError:
                    pass

            if not self.executor.is_market_open():
                logger.info("🔔 El mercado ha cerrado. Deteniendo WebSockets de la sesión actual.")
                await self.stop()
                break

            oldest_update = min(
                (v["updated_at"] for v in self.indicator_cache.values()),
                default=0
            )
            if time.time() - oldest_update >= 600:
                await asyncio.to_thread(self.refresh_context_cache)

    async def start_session(self):
        """Inicia la sesión en tiempo real mientras el mercado esté abierto."""
        self.is_running = True
        self.session_start_ts = time.time()
        logger.info("🚀 Iniciando Motor Cuantitativo Real-Time V5.5 Cloud 24/7 (WebSockets IEX)...")

        await asyncio.to_thread(self.refresh_context_cache)
        all_monitored = list(set(self.watchlist + list(self.open_positions_cache.keys()) + [BENCHMARK_SYMBOL]))

        self.stock_stream = StockDataStream(self.api_key, self.secret_key, feed=DataFeed.IEX)
        self.trading_stream = TradingStream(self.api_key, self.secret_key, paper=True)

        logger.info(f"📡 Suscribiendo barras (1m) y trades (tick-by-tick) en vivo para: {all_monitored}")
        self.stock_stream.subscribe_bars(self.on_minute_bar, *all_monitored)
        self.stock_stream.subscribe_trades(self.on_realtime_trade, *all_monitored)
        self.trading_stream.subscribe_trade_updates(self.on_trade_update)

        try:
            await asyncio.gather(
                self.stock_stream._run_forever(),
                self.trading_stream._run_forever(),
                self._background_maintenance_loop(),
                return_exceptions=True
            )
        except Exception as e:
            logger.error(f"Error en bucle principal de WebSockets: {e}")
        finally:
            self.is_running = False

    async def stop(self):
        """Cierra las conexiones WebSocket de forma segura."""
        self.is_running = False
        try:
            if self.stock_stream:
                await self.stock_stream.stop_ws()
            if self.trading_stream:
                await self.trading_stream.stop_ws()
        except Exception as e:
            logger.warning(f"Aviso al cerrar WebSockets: {e}")
