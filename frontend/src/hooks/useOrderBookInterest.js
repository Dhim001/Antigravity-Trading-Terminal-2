import { useEffect } from 'react';
import { registerOrderBookConsumer } from '../services/orderBookInterest';

/** Keep L2 snapshots in the Zustand store while this component is mounted. */
export function useOrderBookInterest() {
  useEffect(() => registerOrderBookConsumer(), []);
}
