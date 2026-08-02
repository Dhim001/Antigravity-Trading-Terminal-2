/**
 * Unified logger utility — level-gated to prevent production log spam.
 */
const IS_DEV = typeof import.meta !== 'undefined' && Boolean(import.meta.env?.DEV);

export const logger = {
  log: (...args) => {
    if (IS_DEV) console.log(...args);
  },
  info: (...args) => {
    if (IS_DEV) console.info(...args);
  },
  warn: (...args) => {
    if (IS_DEV) console.warn(...args);
  },
  error: (...args) => {
    console.error(...args);
  },
};

export default logger;
