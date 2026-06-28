import { useCallback, useEffect } from 'react';
import { useSettingsStore } from '../store/useSettingsStore';

/**
 * Hook to manage detached panels (pop-outs)
 */
export function useDetachedPanels() {
  const { workspace } = useSettingsStore(s => s.settings);
  const updateWorkspace = useSettingsStore(s => s.updateWorkspace);
  
  // Keep track of detached tabs in workspace settings
  // Use a string array 'detachedTabs'
  const detachedTabs = workspace?.detachedTabs || [];
  
  const isDetached = useCallback((tabId) => {
    return detachedTabs.includes(tabId);
  }, [detachedTabs]);
  
  const detach = useCallback((tabId) => {
    if (!isDetached(tabId)) {
      updateWorkspace({ detachedTabs: [...detachedTabs, tabId] });
    }
  }, [detachedTabs, isDetached, updateWorkspace]);
  
  const attach = useCallback((tabId) => {
    if (isDetached(tabId)) {
      updateWorkspace({ detachedTabs: detachedTabs.filter(id => id !== tabId) });
    }
  }, [detachedTabs, isDetached, updateWorkspace]);
  
  return {
    detachedTabs,
    isDetached,
    detach,
    attach
  };
}
