"""Геокодирование через OSM Nominatim и расчёт дорожного расстояния через публичный OSRM."""
import asyncio
import math
import time

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
USER_AGENT = "address-distance-calculator/1.0"

# Nominatim требует не больше 1 запроса в секунду с одного клиента.
_nominatim_lock = asyncio.Lock()
_nominatim_last_request = 0.0


class GeoApiError(Exception):
    pass


async def geocode(client: httpx.AsyncClient, address: str) -> tuple[float, float]:
    """Возвращает (longitude, latitude) для адреса."""
    global _nominatim_last_request
    async with _nominatim_lock:
        wait = 1.1 - (time.monotonic() - _nominatim_last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        resp = await client.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
        )
        _nominatim_last_request = time.monotonic()
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise GeoApiError(f"Адрес не найден: {address!r}")
    return float(results[0]["lon"]), float(results[0]["lat"])


async def geocode_with_cities(
    client: httpx.AsyncClient, address: str, cities: list[str]
) -> tuple[float, float]:
    """Пробует геокодировать адрес с каждым городом-кандидатом по очереди, затем без города."""
    candidates = [f"{city}, {address}" for city in cities] + [address]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return await geocode(client, candidate)
        except GeoApiError as exc:
            last_error = exc
    raise last_error or GeoApiError(f"Адрес не найден: {address!r}")


async def route_distance_km(
    client: httpx.AsyncClient, origin: tuple[float, float], destination: tuple[float, float]
) -> float:
    """Расстояние по автомобильному маршруту в км (точное, без округления), через OSRM."""
    coords = f"{origin[0]},{origin[1]};{destination[0]},{destination[1]}"
    resp = await client.get(f"{OSRM_ROUTE_URL}/{coords}", params={"overview": "false"})
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise GeoApiError(f"Не удалось получить маршрут: {data}")
    distance_meters = data["routes"][0]["distance"]
    return distance_meters / 1000.0


def round_up_km(distance_km: float) -> int:
    return math.ceil(distance_km)


async def compute_distance(
    client: httpx.AsyncClient,
    address_from: str,
    address_to: str,
    cities: list[str],
    sem: asyncio.Semaphore,
) -> int:
    """Геокодирует оба адреса (Nominatim, перебирая города-кандидаты) и считает
    расстояние по дороге (OSRM), округлённое вверх до целого км."""
    async with sem:
        origin = await geocode_with_cities(client, address_from, cities)
        destination = await geocode_with_cities(client, address_to, cities)
        distance_km = await route_distance_km(client, origin, destination)
        return round_up_km(distance_km)
