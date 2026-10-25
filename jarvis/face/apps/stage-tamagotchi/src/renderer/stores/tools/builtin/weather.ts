import type { Tool } from '@xsai/shared-chat'

import { tool } from '@xsai/tool'
import { z } from 'zod'

import { fetchWeather, weatherForecastDays } from './weather-api'

export type { WeatherDay, WeatherReport, WeatherView } from './weather-api'
export { fetchWeather, geocodeCity, mapWmoCode } from './weather-api'

// -- LLM Tool --

/** The name of the weather tool. The chat shows the results of calls with this name as a forecast card. */
export const WEATHER_TOOL_NAME = 'get_weather'

const weatherParams = z.object({
  // NOTICE:
  // Open-Meteo geocoding finds English names reliably. On 2026-10-13, "上海" with `language=en` found nothing,
  // "上海市" with `language=zh` found a place in the United States, and "東京" with `language=ja` found nothing.
  // Source: https://geocoding-api.open-meteo.com/v1/search
  // Remove this rule when the tool resolves names in other languages, for example with a second search.
  city: z.string().describe('English name of the city, for example "Shanghai". Translate a name in another language to English.'),
  days: z.number().int().min(weatherForecastDays.min).max(weatherForecastDays.max).describe('Forecast days from today: 1 is today only, 2 includes tomorrow.'),
})

/**
 * Returns the weather as JSON for the model. It does not show a widget: the chat draws the forecast card from the result
 * (`weatherToolCallWidget`). JARVIS no longer sends the model `stage_widgets`, so the description does not mention it.
 */
async function executeGetWeather(input: { city: string, days: number }): Promise<string> {
  return JSON.stringify(await fetchWeather(input.city, input.days))
}

const tools: Promise<Tool>[] = [
  tool({
    name: WEATHER_TOOL_NAME,
    // JARVIS: the chat draws the forecast card from this result; the model only reports it in a sentence.
    description: 'Get the current weather and a daily forecast for a city. Returns JSON with `location`, `current`, and `daily` (`daily[0]` is today). The chat shows the forecast card by itself.',
    execute: executeGetWeather,
    parameters: weatherParams,
  }),
]

export const weatherTools = async () => Promise.all(tools)
