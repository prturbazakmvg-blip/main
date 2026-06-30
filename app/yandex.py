"""Клиенты для Яндекс Geocoder и Router API."""
import asyncio
import math

import httpx

GEOCODE_URL = "https://geocode-maps.yandex.ru/1.x/"
ROUTER_URL = "https://api.routing.yandex.net/v2/route"

GEOCODE_CONCURRENCY = 5
ROUTE_CONCURRENCY = 5


class YandexApiError(Exception):
    pass


async def geocode(client: httpx.AsyncClient, api_key: str, address: str) -> tuple[float, float]:
    """Возвращает (longitude, latitude) для адреса."""
    resp = await client.get(
        GEOCODE_URL,
        params={"apikey": api_key, "geocode": address, "format": "json", "results": 1},
    )
    resp.raise_for_status()
    data = resp.json()
    members = data["response"]["GeoObjectCollection"]["featureMember"]
    if not members:
        raise YandexApiError(f"Адрес не найден: {address!r}")
    pos = members[0]["GeoObject"]["Point"]["pos"]
    lon_str, lat_str = pos.split(" ")
    return float(lon_str), float(lat_str)


async def route_distance_km(
    client: httpx.AsyncClient, api_key: str, origin: tuple[float, float], destination: tuple[float, float]
) -> float:
    """Расстояние по автомобильному маршруту в км (точное, без округления)."""
    waypoints = f"{origin[1]},{origin[0]}|{destination[1]},{destination[0]}"
    resp = await client.get(
        ROUTER_URL,
        params={"apikey": api_key, "waypoints": waypoints, "mode": "driving"},
    )
    resp.raise_for_status()
    data = resp.json()
    routes = data.get("route", {}).get("legs") or data.get("routes")
    # Формат ответа Router API: {"route": {"legs": [...], "distance": {"value": meters}, ...}}
    distance_meters = None
    if "route" in data and isinstance(data["route"], dict) and "distance" in data["route"]:
        distance_meters = data["route"]["distance"]["value"]
    elif "routes" in data and data["routes"]:
        distance_meters = data["routes"][0]["distance"]["value"]
    if distance_meters is None:
        raise YandexApiError(f"Не удалось получить маршрут: {data}")
    return distance_meters / 1000.0


def round_up_km(distance_km: float) -> int:
    return math.ceil(distance_km)


async def compute_distance(
    client: httpx.AsyncClient,
    geocoder_key: str,
    router_key: str,
    address_from: str,
    address_to: str,
    sem: asyncio.Semaphore,
) -> int:
    """Геокодирует оба адреса и считает расстояние по дороге, округлённое вверх до целого км."""
    async with sem:
        origin = await geocode(client, geocoder_key, address_from)
        destination = await geocode(client, geocoder_key, address_to)
        distance_km = await route_distance_km(client, router_key, origin, destination)
        return round_up_km(distance_km)
