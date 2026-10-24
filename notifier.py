import os
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from logger import logger

load_dotenv()


class DiscordNotifier:
    def __init__(self):
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip().strip('"').strip("'")
        self.enabled = bool(self.webhook_url and self.webhook_url.startswith("http"))

    def send_embed(self, title: str, description: str, color: int = 0x3498DB, fields: list[dict] = None) -> bool:
        """
        Envía un mensaje enriquecido (Embed) a Discord vía Webhook.
        Colores sugeridos:
        - Verde (Compra / Éxito): 0x2ECC71
        - Dorado (Profit Lock 1.4R): 0xF1C40F
        - Azul (Scanner / Info): 0x3498DB
        - Naranja (Cierre Posición): 0xE67E22
        - Rojo (Stop / Circuit Breaker / Alerta): 0xE74C3C
        """
        if not self.enabled:
            logger.debug("DiscordNotifier desactivado (DISCORD_WEBHOOK_URL no configurado en .env).")
            return False

        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Agente Cuantitativo V5.5 • Alpaca Paper Trading"}
        }
        if fields:
            embed["fields"] = fields

        payload = {
            "username": "QuantBot V5.5",
            "embeds": [embed]
        }

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=8)
            if resp.status_code in (200, 204):
                return True
            logger.warning(f"Discord devolvió código {resp.status_code}: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"Error enviando notificación a Discord: {e}")
            return False

    def notify_scanner_update(self, watchlist: list[str], macro_status: str, open_positions: list[str]):
        fields = [
            {"name": "🎯 Top 4 Watchlist Hoy", "value": ", ".join(watchlist) if watchlist else "Ninguno", "inline": False},
            {"name": "🌍 Escudo Macro (SPY)", "value": macro_status, "inline": False},
            {"name": "💼 Posiciones en Cartera", "value": ", ".join(open_positions) if open_positions else "Sin posiciones abiertas", "inline": False}
        ]
        self.send_embed(
            title="🔍 Escáner Cuantitativo Diario Completado",
            description="El motor V5.5 ha evaluado el universo de 12 activos con backtesting anual y actualizó su radar operativo.",
            color=0x3498DB,
            fields=fields
        )

    def notify_buy_order(self, order_plan, confidence: float, reason: str):
        fields = [
            {"name": "📈 Activo", "value": f"`{order_plan.symbol}`", "inline": True},
            {"name": "📦 Cantidad", "value": f"`{order_plan.qty}` acciones", "inline": True},
            {"name": "🎯 Confianza IA", "value": f"`{confidence*100:.0f}%`", "inline": True},
            {"name": "💵 Entrada Est.", "value": f"`${order_plan.entry_price:.2f}`", "inline": True},
            {"name": "🛑 Stop Loss (2x ATR)", "value": f"`${order_plan.stop_loss:.2f}`", "inline": True},
            {"name": "💰 Take Profit (4x ATR)", "value": f"`${order_plan.take_profit:.2f}`", "inline": True},
            {"name": "🧠 Tesis de Entrada", "value": reason, "inline": False}
        ]
        self.send_embed(
            title=f"🚀 NUEVA ORDEN BRACKET ENVIADA: {order_plan.symbol}",
            description=f"Se ha disparado una compra automática en tiempo real para **{order_plan.symbol}**.",
            color=0x2ECC71,
            fields=fields
        )

    def notify_profit_lock(self, symbol: str, current_r: float, live_price: float, lock_price: float, server_ok: bool):
        fields = [
            {"name": "📈 Activo", "value": f"`{symbol}`", "inline": True},
            {"name": "⚡ Ganancia Flotante", "value": f"`+{current_r:.2f}R` (`${live_price:.2f}`)", "inline": True},
            {"name": "🔒 Nuevo Stop (+0.1R)", "value": f"`${lock_price:.2f}`", "inline": True},
            {"name": "🛡️ Estado Servidor Alpaca", "value": "Stop Loss actualizado en servidor" if server_ok else "Protegido en memoria/disco local", "inline": False}
        ]
        self.send_embed(
            title=f"🔒 PROFIT LOCK 1.4R ACTIVADO: {symbol}",
            description="La posición superó +1.4R. La operación ya está libre de riesgo y asegurada en ganancia.",
            color=0xF1C40F,
            fields=fields
        )

    def notify_position_closed(self, symbol: str, live_price: float, exit_reason: str):
        self.send_embed(
            title=f"💰 POSICIÓN CERRADA EN VIVO: {symbol}",
            description=f"Se ejecutó el cierre automático de **{symbol}** a `${live_price:.2f}`.\n**Motivo:** {exit_reason}",
            color=0xE67E22
        )

    def notify_circuit_breaker(self, equity: float, last_equity: float, dd_pct: float):
        self.send_embed(
            title="🚨 CIRCUIT BREAKER DIARIO ACTIVADO (-2.0% DD)",
            description=(
                f"El portafolio alcanzó un drawdown diario de `{dd_pct*100:.2f}%` "
                f"(Equity: `${equity:,.2f}` vs Ayer: `${last_equity:,.2f}`).\n"
                f"Todas las órdenes abiertas fueron canceladas y el bot permanecerá en pausa preventiva por hoy."
            ),
            color=0xE74C3C
        )
