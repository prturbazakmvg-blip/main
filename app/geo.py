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


def _matches_city(result: dict, city: str | None) -> bool:
    """Проверяет, что найденный результат действительно относится к нужному городу."""
    if not city:
        return True
    city_norm = city.strip().lower()
    addr = result.get("address", {})
    candidates = [
        addr.get("city"), addr.get("town"), addr.get("village"),
        addr.get("municipality"), addr.get("county"),
    ]
    if any(c and city_norm in c.lower() for c in candidates):
        return True
    return city_norm in (result.get("display_name") or "").lower()


async def _nominatim_query(client: httpx.AsyncClient, query: str) -> list[dict]:
    global _nominatim_last_request
    async with _nominatim_lock:
        wait = 1.1 - (time.monotonic() - _nominatim_last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        resp = await client.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 5, "addressdetails": 1},
            headers={"User-Agent": USER_AGENT},
        )
        _nominatim_last_request = time.monotonic()
    resp.raise_for_status()
    return resp.json()


async def geocode(client: httpx.AsyncClient, address: str, city: str | None = None) -> tuple[float, float]:
    """Возвращает (longitude, latitude) для адреса. Если указан город, отбрасывает
    результаты, относящиеся к другим городам, чтобы не перепутать одноимённые улицы."""
    results = await _nominatim_query(client, address)
    if not results:
        raise GeoApiError(f"Адрес не найден: {address!r}")
    for result in results:
        if _matches_city(result, city):
            return float(result["lon"]), float(result["lat"])
    if city:
        raise GeoApiError(f"Адрес не найден в городе {city!r}: {address!r}")
    return float(results[0]["lon"]), float(results[0]["lat"])


async def geocode_with_cities(
    client: httpx.AsyncClient, address: str, cities: list[str]
) -> tuple[float, float]:
    """Пробует геокодировать адрес с каждым городом-кандидатом по очереди, затем без города.
    Для каждого кандидата проверяется, что найденный результат действительно относится
    к запрошенному городу (а не к одноимённой улице в другом месте)."""
    last_error: Exception | None = None
    for city in cities:
        try:
            return await geocode(client, f"{city}, {address}", city=city)
        except GeoApiError as exc:
            last_error = exc
    try:
        return await geocode(client, address)
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
