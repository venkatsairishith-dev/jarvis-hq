export const all = {
  en: 'English',
}

export const localeRemap: Record<string, string> = {
  'en': 'en',
  'en-US': 'en',
  'en-GB': 'en',
  'en-AU': 'en',
  'en-IN': 'en',
}

export function resolveSupportedLocale(
  locale: string | null | undefined,
  supportedLocales: readonly string[],
  fallbackLocale = 'en',
): string {
  const normalizedLocale = localeRemap[locale ?? fallbackLocale] ?? locale ?? fallbackLocale

  return supportedLocales.includes(normalizedLocale)
    ? normalizedLocale
    : fallbackLocale
}

export { createFallbackOptions } from './fallback'
export { serializeStartupFallbackLocales } from './startup-fallback'
