"""Weather via Open-Meteo (free, no API key)."""
from __future__ import annotations

import requests

WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog",
    48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def geocode(name: str) -> dict | None:
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": name, "count": 1}, timeout=10)
    r.raise_for_status()
    res = (r.json().get("results") or [None])[0]
    return res


def register(reg, config) -> None:
    @reg.tool(
        "get_weather",
        "Current weather and a short daily forecast for a place. Defaults to the user's home "
        "location if none is given.",
        {
            "location": {"type": "string", "description": "City name, e.g. 'Singapore' or 'Phuket'"},
            "days": {"type": "integer", "minimum": 1, "maximum": 7},
        },
    )
    def get_weather(ctx, location: str = "", days: int = 3):
        location = location or ctx.config["assistant"].get("home_location") or ""
        if not location:
            return "No location given and no home_location configured. Ask the user where."
        place = geocode(location)
        if not place:
            return f"Couldn't find a place called {location!r}."
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto", "forecast_days": int(days),
        }, timeout=10)
        r.raise_for_status()
        d = r.json()
        cur = d["current"]
        daily = d["daily"]
        return {
            "place": f"{place['name']}, {place.get('country', '')}",
            "now": {
                "temp_c": cur["temperature_2m"],
                "feels_like_c": cur["apparent_temperature"],
                "humidity": cur["relative_humidity_2m"],
                "wind_kmh": cur["wind_speed_10m"],
                "conditions": WMO.get(cur["weather_code"], str(cur["weather_code"])),
            },
            "forecast": [
                {"date": daily["time"][i],
                 "conditions": WMO.get(daily["weather_code"][i], ""),
                 "min_c": daily["temperature_2m_min"][i],
                 "max_c": daily["temperature_2m_max"][i],
                 "rain_chance": daily["precipitation_probability_max"][i]}
                for i in range(len(daily["time"]))
            ],
        }
