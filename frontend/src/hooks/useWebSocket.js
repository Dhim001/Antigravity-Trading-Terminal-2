import { useEffect } from 'react';
import { connectWebSocket } from '../services/websocket';
import { runBootstrap } from '../api/bootstrap';
import { useStore } from '../store/useStore';
import { WS_URL } from '../api/config';

const REST_REFRESH_MS = 30000;

export const useWebSocket = (url = WS_URL) => {
  useEffect(() => {
    connectWebSocket(url);
    return () => {
      // Keep connection active across component updates.
    };
  }, [url]);

  // While WS is down, refresh REST snapshots so prices/account don't freeze.
  useEffect(() => {
    const id = setInterval(() => {
      if (useStore.getState().connectionStatus !== 'connected') {
        runBootstrap({ offline: true, skipCandles: true }).catch(() => {});
      }
    }, REST_REFRESH_MS);
    return () => clearInterval(id);
  }, []);
};
