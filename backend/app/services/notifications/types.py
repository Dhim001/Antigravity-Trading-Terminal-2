"""Notification event type constants."""

from __future__ import annotations

TRADE_FILL = "trade_fill"
SL_TP_TRIGGER = "sl_tp_trigger"
BOT_STATUS = "bot_status"
BOT_LOG_WARN = "bot_log_warn"
BOT_LOG_ERROR = "bot_log_error"
KILL_SWITCH = "kill_switch"
SAFE_MODE = "safe_mode"
EMERGENCY_STOP = "emergency_stop"
DAILY_DIGEST = "daily_digest"
ALERT_RULE = "alert_rule"
TEST = "test"

REALTIME_EVENT_TYPES = (
    TRADE_FILL,
    SL_TP_TRIGGER,
    BOT_STATUS,
    BOT_LOG_WARN,
    BOT_LOG_ERROR,
    KILL_SWITCH,
    SAFE_MODE,
    EMERGENCY_STOP,
    ALERT_RULE,
)

ALL_EVENT_TYPES = REALTIME_EVENT_TYPES + (DAILY_DIGEST,)

CHANNEL_WEBHOOK = "webhook"
CHANNEL_TELEGRAM = "telegram"
CHANNEL_EMAIL = "email"
CHANNEL_PUSH = "push"

ALL_CHANNEL_TYPES = (CHANNEL_WEBHOOK, CHANNEL_TELEGRAM, CHANNEL_EMAIL, CHANNEL_PUSH)
