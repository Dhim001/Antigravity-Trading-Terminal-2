"""Example custom strategy plugin.

Deploy with strategy=CUSTOM and config: {"module": "example_rsi_reversal", ...}
Requires ALLOW_CUSTOM_STRATEGIES=true in .env
"""


def evaluate(row, config):
    rsi = row.get(f"RSI_{config.get('rsi_length', 14)}")
    close = row.get("close")
    if rsi is None or close is None:
        return {"signal": "NONE"}
    if rsi < config.get("oversold", 30):
        return {"signal": "BUY", "stop_loss_distance": close * 0.02}
    if rsi > config.get("overbought", 70):
        return {"signal": "SELL", "stop_loss_distance": close * 0.02}
    return {"signal": "NONE"}
