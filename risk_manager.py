from dataclasses import dataclass

@dataclass
class OrderPlan:
    symbol: str
    side: str
    qty: int
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float

class RiskManager:
    def __init__(self, max_risk_per_trade: float = 0.01, rr_ratio: float = 2.0, max_position_pct: float = 0.30):
        self.max_risk_per_trade = max_risk_per_trade
        self.rr_ratio = rr_ratio
        self.max_position_pct = max_position_pct

    def build_order_plan(self, symbol: str, side: str, equity: float, buying_power: float, 
                         current_price: float, atr: float) -> OrderPlan | None:
        if current_price <= 0 or atr <= 0 or equity <= 0:
            return None

        # 2.0x ATR evita barridos por ruido intradía en acciones tecnológicas
        stop_distance = round(atr * 2.0, 2)
        if stop_distance < 0.05:
            stop_distance = round(current_price * 0.015, 2)

        if side == "buy":
            stop_loss = round(current_price - stop_distance, 2)
            take_profit = round(current_price + (stop_distance * self.rr_ratio), 2)
        else:
            return None

        risk_dollars = equity * self.max_risk_per_trade
        raw_qty = int(risk_dollars // stop_distance)

        max_dollars_allowed = min(equity * self.max_position_pct, buying_power * 0.95)
        max_qty_by_capital = int(max_dollars_allowed // current_price)

        final_qty = min(raw_qty, max_qty_by_capital)

        if final_qty < 1:
            return None

        return OrderPlan(
            symbol=symbol,
            side=side,
            qty=final_qty,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=round(final_qty * stop_distance, 2)
        )

    def calculate_order_plan(self, symbol: str, entry_price: float, atr: float,
                             account_equity: float, buying_power: float, side: str = "buy") -> OrderPlan | None:
        """Calcula el plan de orden con las firmas usadas por RealTimeTradingEngine."""
        return self.build_order_plan(
            symbol=symbol,
            side=side,
            equity=account_equity,
            buying_power=buying_power,
            current_price=entry_price,
            atr=atr
        )
