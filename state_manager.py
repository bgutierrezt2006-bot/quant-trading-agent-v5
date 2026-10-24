import os
import json
from datetime import datetime
from logger import logger

STATE_FILE = "bot_state.json"


class StateManager:
    def __init__(self, filepath: str = STATE_FILE):
        self.filepath = filepath
        self.state = {
            "profit_locks": {},
            "updated_at": None
        }
        if os.path.exists(self.filepath):
            self.load_state()
        else:
            self.save_state()

    def load_state(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "profit_locks" in data:
                        self.state = data
                        logger.info(
                            f"💾 [STATE MANAGER] Estado recuperado desde disco: "
                            f"{len(self.state['profit_locks'])} Profit Locks activos."
                        )
            except Exception as e:
                logger.error(f"Error cargando {self.filepath}: {e}")
        return self.state

    def save_state(self):
        try:
            self.state["updated_at"] = datetime.now().isoformat()
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Error guardando {self.filepath}: {e}")

    def set_profit_lock(self, symbol: str, lock_price: float, entry_price: float, server_updated: bool = False):
        self.state["profit_locks"][symbol] = {
            "lock_price": round(float(lock_price), 2),
            "entry_price": round(float(entry_price), 2),
            "server_updated": bool(server_updated),
            "locked_at": datetime.now().isoformat()
        }
        self.save_state()

    def get_profit_lock(self, symbol: str) -> dict | None:
        return self.state["profit_locks"].get(symbol)

    def remove_profit_lock(self, symbol: str):
        if symbol in self.state["profit_locks"]:
            del self.state["profit_locks"][symbol]
            self.save_state()

    def sync_with_open_positions(self, active_symbols: set[str]):
        current_locked = list(self.state["profit_locks"].keys())
        removed = False
        for sym in current_locked:
            if sym not in active_symbols:
                del self.state["profit_locks"][sym]
                removed = True
                logger.info(f"🧹 [STATE MANAGER] Limpiando Profit Lock histórico de {sym} (posición ya cerrada).")
        if removed:
            self.save_state()
