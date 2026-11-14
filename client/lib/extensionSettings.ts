import { browser } from 'wxt/browser';
import { DEFAULT_SETTINGS, type Settings } from './types';

export type BackendMode = Settings['backendMode'];

export type ExtensionSettings = Settings;

const KEY = 'settings';

/** A build with `WXT_API_URL` set defaults to the local index from that backend. */
const BUILD_API_URL: string | undefined = import.meta.env.WXT_API_URL || undefined;

export const EXTENSION_DEFAULTS: ExtensionSettings = {
  ...DEFAULT_SETTINGS,
  backendMode: BUILD_API_URL ? 'local' : DEFAULT_SETTINGS.backendMode,
  backendUrl: BUILD_API_URL ?? DEFAULT_SETTINGS.backendUrl,
};

export async function getExtensionSettings(): Promise<ExtensionSettings> {
  const stored = (await browser.storage.local.get(KEY))[KEY] as Partial<ExtensionSettings> | undefined;
  return { ...EXTENSION_DEFAULTS, ...stored };
}

export async function updateExtensionSettings(patch: Partial<ExtensionSettings>): Promise<void> {
  await browser.storage.local.set({ [KEY]: { ...(await getExtensionSettings()), ...patch } });
}
