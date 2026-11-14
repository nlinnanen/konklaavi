import { browser } from 'wxt/browser';
import { DEFAULT_SETTINGS, type Settings } from './types';

export type BackendMode = Settings['backendMode'];

const KEY = 'settings';

/** A build with `WXT_API_URL` set defaults to the local index from that backend. */
const BUILD_API_URL: string | undefined = import.meta.env.WXT_API_URL || undefined;

export const EXTENSION_DEFAULTS: Settings = {
  ...DEFAULT_SETTINGS,
  backendMode: BUILD_API_URL ? 'local' : DEFAULT_SETTINGS.backendMode,
  backendUrl: BUILD_API_URL ?? DEFAULT_SETTINGS.backendUrl,
};

export async function getSettings(): Promise<Settings> {
  const stored = (await browser.storage.local.get(KEY))[KEY] as Partial<Settings> | undefined;
  return { ...EXTENSION_DEFAULTS, ...stored };
}

export async function updateSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = { ...(await getSettings()), ...patch };
  await browser.storage.local.set({ [KEY]: next });
  return next;
}

export function onSettingsChanged(cb: (s: Settings) => void): void {
  browser.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes[KEY]) cb({ ...EXTENSION_DEFAULTS, ...(changes[KEY].newValue as Partial<Settings>) });
  });
}
