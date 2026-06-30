"""Геокодирование через Яндекс и расчёт дорожного расстояния через публичный OSRM."""
import asyncio
import math

import httpx

GEOCODE_URL = "https://geocode-maps.yandex.ru/1.x/"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"


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
    client: httpx.AsyncClient, origin: tuple[float, float], destination: tuple[float, float]
) -> float:
    """Расстояние по автомобильному маршруту в км (точное, без округления), через OSRM."""
    coords = f"{origin[0]},{origin[1]};{destination[0]},{destination[1]}"
    resp = await client.get(f"{OSRM_ROUTE_URL}/{coords}", params={"overview": "false"})
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise YandexApiError(f"Не удалось получить маршрут: {data}")
    distance_meters = data["routes"][0]["distance"]
    return distance_meters / 1000.0


def round_up_km(distance_km: float) -> int:
    return math.ceil(distance_km)


async def compute_distance(
    client: httpx.AsyncClient,
    geocoder_key: str,
    address_from: str,
    address_to: str,
    sem: asyncio.Semaphore,
) -> int:
    """Геокодирует оба адреса (Яндекс) и считает расстояние по дороге (OSRM), округлённое вверх до целого км."""
    async with sem:
        origin = await geocode(client, geocoder_key, address_from)
        destination = await geocode(client, geocoder_key, address_to)
        distance_km = await route_distance_km(client, origin, destination)
        return round_up_km(distance_km)
