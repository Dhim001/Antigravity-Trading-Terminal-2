import { useEffect } from 'react';
import { useTheme } from 'next-themes';
import { useSettingsStore } from '../store/useSettingsStore';

/** Syncs persisted settings → DOM CSS vars and next-themes class. */
export default function SettingsBootstrap() {
  const settings = useSettingsStore((s) => s.settings);
  const applyToDOM = useSettingsStore((s) => s.applyToDOM);
  const setResolvedTheme = useSettingsStore((s) => s.setResolvedTheme);
  const { setTheme, resolvedTheme } = useTheme();

  useEffect(() => {
    applyToDOM();
  }, [settings, applyToDOM]);

  useEffect(() => {
    if (settings.theme) {
      setTheme(settings.theme);
    }
  }, [settings.theme, setTheme]);

  useEffect(() => {
    if (!resolvedTheme) return;
    setResolvedTheme(resolvedTheme === 'light' ? 'light' : 'dark');
  }, [resolvedTheme, setResolvedTheme]);

  return null;
}
