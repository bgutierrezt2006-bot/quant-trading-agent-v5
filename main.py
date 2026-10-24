import sys
import time
import asyncio
import schedule
from logger import logger
from market_scanner import AutonomousMarketScanner
from execution import AlpacaExecutor
from realtime_stream import RealTimeTradingEngine
from notifier import DiscordNotifier

ACTIVE_WATCHLIST = ["MSFT", "AAPL", "GOOGL", "AVGO"]


def refresh_autonomous_watchlist() -> list[str]:
    """Ejecuta el escáner cuantitativo anual para elegir el Top 4 de activos."""
    global ACTIVE_WATCHLIST
    try:
        logger.info("🔍 Ejecutando AutonomousMarketScanner para seleccionar el Top 4 de mercados...")
        scanner = AutonomousMarketScanner()
        if hasattr(scanner, "select_top_assets"):
            selected = scanner.select_top_assets(top_n=4)
        elif hasattr(scanner, "scan_market"):
            selected = scanner.scan_market(top_n=4)
        else:
            selected = scanner.get_top_symbols(top_n=4)

        if selected:
            ACTIVE_WATCHLIST = selected
            logger.info(f"✅ ACTIVE_WATCHLIST actualizada autónomamente: {ACTIVE_WATCHLIST}")
            notifier = DiscordNotifier()
            if notifier.enabled:
                notifier.send_embed(
                    title="🎯 Watchlist Diaria Actualizada",
                    description=f"El escáner cuantitativo ha seleccionado el Top 4 de activos para hoy: **{', '.join(selected)}**.",
                    color=0x3498DB
                )
        else:
            logger.warning(f"⚠️ Escáner no devolvió activos nuevos. Manteniendo: {ACTIVE_WATCHLIST}")
    except Exception as e:
        logger.error(f"Error ejecutando AutonomousMarketScanner: {e}")
    return ACTIVE_WATCHLIST


def run_diagnostic_once():
    """Modo de prueba rápida (--test-once) para verificar caché, posiciones, escudo macro y persistencia en disco."""
    logger.info("🧪 Ejecutando diagnóstico completo V5.5 Cloud & Telemetry (--test-once)...")
    watchlist = refresh_autonomous_watchlist()
    engine = RealTimeTradingEngine(watchlist)
    engine.refresh_context_cache()

    # 1. Escudo Macro (SPY)
    macro_ok, macro_msg = engine.is_macro_regime_healthy()
    status_icon = "🟢" if macro_ok else "🔴"
    logger.info(f"🌍 [ESCUDO MACRO SPY] Estado: {status_icon} {macro_msg}")

    # 2. Filtro de Ruido de Apertura (09:30 - 09:45 AM NY)
    noise_window = engine.is_opening_noise_window()
    if noise_window:
        logger.info("⏳ [FILTRO APERTURA NY] Ventana 9:30-9:45 AM NY ACTIVA: Entradas bloqueadas por volatilidad de apertura.")
    else:
        logger.info("🕒 [FILTRO APERTURA NY] Fuera de ventana de ruido de apertura (Operativa normal permitida).")

    # 3. Evaluación de Watchlist con IA
    for sym in engine.watchlist:
        if sym not in engine.indicator_cache:
            continue
        data = engine.indicator_cache[sym]
        metrics = data["metrics"]
        news = data["news"]
        web_intel = engine.research_cache.get(sym, {})
        decision = engine.brain.evaluate_opportunity(
            symbol=sym,
            metrics=metrics,
            news=news,
            external_intel=web_intel
        )
        logger.info(
            f"📊 [DIAGNÓSTICO {sym}] Close: ${metrics['close']:.2f} | RSI: {metrics['rsi']:.2f} | "
            f"SMA50: ${metrics['sma_50']:.2f} | Acción: {decision.action} "
            f"(Conf: {decision.confidence:.2f}) -> {decision.reason}"
        )

    # 4. Auditoría de Posiciones y Profit Locks en bot_state.json
    open_positions = list(engine.open_positions_cache.keys())
    logger.info(f"💼 Posiciones abiertas detectadas en cartera: {open_positions}")

    for sym, pos in engine.open_positions_cache.items():
        if sym in engine.indicator_cache:
            metrics = engine.indicator_cache[sym]["metrics"]
            live_price = float(metrics["close"])
            entry_price = float(pos.avg_entry_price)
            atr = float(metrics.get("atr", 0.0))
            risk_1r = 2.0 * atr
            r_mult = ((live_price - entry_price) / risk_1r) if risk_1r > 0 else 0.0
            lock_status = engine.state_manager.get_profit_lock(sym)
            lock_info = f"Profit Lock Activo (${lock_status['lock_price']:.2f})" if lock_status else "Sin Profit Lock activo"
            logger.info(
                f"🔎 [AUDITORÍA POSICIÓN {sym}] Entry: ${entry_price:.2f} | Live: ${live_price:.2f} | "
                f"Retorno R: {r_mult:+.2f}R | 1R = ${risk_1r:.2f} | Estado: {lock_info}"
            )

    persisted_locks = engine.state_manager.state.get("profit_locks", {})
    logger.info(f"💾 Profit Locks registrados en bot_state.json: {persisted_locks}")

    # 5. Notificación Telemetría Discord
    engine.notifier.notify_scanner_update(watchlist, macro_msg, open_positions)
    logger.info("✅ Diagnóstico V5.5 Cloud & Telemetry completado con éxito.")


def main():
    logger.info("==================================================================")
    logger.info("🤖 AGENTE DE TRADING CUANTITATIVO AUTÓNOMO - VERSIÓN 5.5 TELEMETRY")
    logger.info("==================================================================")

    if "--test-once" in sys.argv:
        run_diagnostic_once()
        return

    executor = AlpacaExecutor()
    refresh_autonomous_watchlist()

    # Programar escaneo diario antes de la apertura de NY
    schedule.every().day.at("09:00").do(refresh_autonomous_watchlist)

    while True:
        try:
            schedule.run_pending()

            if executor.check_circuit_breaker():
                logger.warning("🚨 Circuit Breaker Diario activo. Esperando 15 minutos...")
                time.sleep(900)
                continue

            if executor.is_market_open():
                logger.info("🟢 Mercado ABIERTO. Conectando Motor WebSocket Continuo V5.5...")
                engine = RealTimeTradingEngine(ACTIVE_WATCHLIST)
                asyncio.run(engine.start_session())
                logger.info("⏸️ Sesión de WebSockets finalizada. Volviendo a modo vigilia...")
            else:
                logger.info("🌙 Mercado CERRADO. En vigilia activa (próximo chequeo en 60s)...")
                time.sleep(60)

        except KeyboardInterrupt:
            logger.info("🛑 Agente detenido manualmente por el usuario.")
            break
        except Exception as e:
            logger.error(f"❌ Error en el orquestador principal: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
