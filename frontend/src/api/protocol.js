/**
 * WebSocket wire protocol — keep in sync with backend/app/api/protocol.py
 */

/** Client → server request actions */
export const Action = Object.freeze({
  PLACE_ORDER: 'place_order',
  CANCEL_ORDER: 'cancel_order',
  UPDATE_POSITION_SL_TP: 'update_position_sl_tp',
  GET_ACCOUNT: 'get_account',
  GET_HISTORY: 'get_history',
  SUBSCRIBE_SYMBOL: 'subscribe_symbol',
  GET_MARKET_HISTORY: 'get_market_history',
  ADMIN_SET_SIMULATION: 'admin_set_simulation',
  ADMIN_SEED_BALANCE: 'admin_seed_balance',
  ADMIN_RESET_SYSTEM: 'admin_reset_system',
  ADMIN_EMERGENCY_STOP: 'admin_emergency_stop',
  ADMIN_GET_STATS: 'admin_get_stats',
  ADMIN_ARCHIVE_BACKFILL: 'admin_archive_backfill',
  ADMIN_ARCHIVE_EXPORT: 'admin_archive_export',
  ADMIN_GET_RECONCILIATION: 'admin_get_reconciliation',
  ADMIN_RECONCILE: 'admin_reconcile',
  ADMIN_RESOLVE_AMBIGUOUS: 'admin_resolve_ambiguous',
  GET_MARKET_TICKS: 'get_market_ticks',
  BOT_CREATE: 'bot_create',
  BOT_STOP: 'bot_stop',
  BOT_PAUSE: 'bot_pause',
  BOT_RESUME: 'bot_resume',
  BOT_STOP_ALL: 'bot_stop_all',
  BOT_GET_DETAIL: 'bot_get_detail',
  BOT_UPDATE_CONFIG: 'bot_update_config',
  BOT_GET_ALL: 'bot_get_all',
  BOT_LIST_ALL: 'bot_list_all',
  RUN_BACKTEST: 'run_backtest',
});

/** Server → client message types */
export const MessageType = Object.freeze({
  TERMINAL_CONFIG: 'terminal_config',
  ORDER_RESULT: 'order_result',
  ACCOUNT_UPDATE: 'account_update',
  TRADE_HISTORY: 'trade_history',
  HISTORY_UPDATE: 'history_update',
  MARKET_UPDATE: 'market_update',
  ORDERBOOK_UPDATE: 'orderbook_update',
  SYSTEM_STATS: 'system_stats',
  BOTS_UPDATE: 'bots_update',
  BOT_DETAIL: 'bot_detail',
  BOT_LOG: 'bot_log',
  BOT_LOGS_HISTORY: 'bot_logs_history',
  BACKTEST_RESULT: 'backtest_result',
  TICKS_UPDATE: 'ticks_update',
  BOTS_HISTORY: 'bots_history',
  ERROR: 'error',
});

/** @typedef {typeof Action[keyof typeof Action]} ActionName */
/** @typedef {typeof MessageType[keyof typeof MessageType]} MessageTypeName */
