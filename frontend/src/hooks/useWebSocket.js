import { useEffect } from 'react';
import { connectWebSocket } from '../services/websocket';
import { WS_URL } from '../api/config';

export const useWebSocket = (url = WS_URL) => {
  useEffect(() => {
    connectWebSocket(url);
    return () => {
      // Keep connection active across component updates.
    };
  }, [url]);
};
