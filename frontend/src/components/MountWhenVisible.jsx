/**
 * Unmount FlexLayout tab children when the tab is not visible.
 * Heavy panels (Algo, ML Training, etc.) stay sticky in FlexLayout's
 * render-on-demand cache; wrapping them here reclaims their React trees,
 * ECharts, and pollers until the tab is selected again. Zustand holds
 * durable state, so remount is cheap.
 */
import { useEffect, useState } from 'react';

export default function MountWhenVisible({ node, children, fallback = null }) {
  const [visible, setVisible] = useState(() => Boolean(node?.isVisible?.()));

  useEffect(() => {
    if (!node?.setEventListener) return undefined;
    const onVisibility = (params) => {
      if (params && typeof params.visible === 'boolean') {
        setVisible(params.visible);
      } else {
        setVisible(Boolean(node.isVisible()));
      }
    };
    setVisible(Boolean(node.isVisible()));
    node.setEventListener('visibility', onVisibility);
    return () => node.removeEventListener('visibility');
  }, [node]);

  if (!visible) return fallback;
  return children;
}
