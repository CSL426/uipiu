"""依品牌找 Nidin 分店：離主揪最近的，或評分相對高的。

「相對評分高」用評價數加權：只有 1 則 5 星的店不該排在 300 則 4.8 星前面。
做法是把每家店的平均星數往一個保守的基準拉，評價越少拉得越多（貝氏平均）。
基準不用全品牌平均：飲料店普遍 4.7～4.8 星，往那裡拉等於沒拉，1 則 5 星照樣贏。
"""

import math
import time
from dataclasses import dataclass
from typing import Any, Literal

from app.nidin import NidinClient

SortBy = Literal["distance", "rating"]
# 評價數少於這個值時，平均星數會明顯被拉向 RATING_PRIOR
RATING_PRIOR_WEIGHT = 20
RATING_PRIOR = 4.0
# 按評分排序時只看這個距離內的店，否則會排出外送不到的遠方分店
RATING_MAX_KM = 8.0
BRANDS_CACHE_SECONDS = 3600.0

_brands_cache: tuple[float, list[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class Origin:
    latitude: float
    longitude: float
    label: str


def clear_brands_cache() -> None:
    global _brands_cache
    _brands_cache = None


async def brands(client: NidinClient) -> list[dict[str, Any]]:
    global _brands_cache
    if _brands_cache is not None and time.monotonic() - _brands_cache[0] < BRANDS_CACHE_SECONDS:
        return _brands_cache[1]
    result = await client.list_brands()
    _brands_cache = (time.monotonic(), result)
    return result


def match_brands(all_brands: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    """完全同名的優先；沒有才用包含關鍵字（品名、簡稱、別名）。"""
    keyword = keyword.strip()
    if not keyword:
        return []

    def names(brand: dict[str, Any]) -> list[str]:
        aliases = brand.get("name_alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        return [str(n) for n in [brand.get("name"), brand.get("name_short"), *aliases] if n]

    exact = [b for b in all_brands if keyword in names(b)]
    return exact or [b for b in all_brands if any(keyword in n for n in names(b))]


def origin_from_addresses(addresses: list[dict[str, Any]]) -> Origin | None:
    for address in addresses:
        try:
            lat, lng = float(address.get("latitude")), float(address.get("longitude"))
        except (TypeError, ValueError):
            continue
        if lat and lng:
            return Origin(lat, lng, str(address.get("loc_name") or address.get("address_short") or "常用地址"))
    return None


def distance_km(origin: Origin, store: dict[str, Any]) -> float | None:
    try:
        lat, lng = float(store["latitude"]), float(store["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    p1, p2 = math.radians(origin.latitude), math.radians(lat)
    dp, dl = p2 - p1, math.radians(lng - origin.longitude)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def _rating(store: dict[str, Any]) -> tuple[float, int]:
    try:
        return float(store.get("rating_average_star") or 0), int(store.get("rating_order_count") or 0)
    except (TypeError, ValueError):
        return 0.0, 0


def is_orderable(store: dict[str, Any]) -> bool:
    return store.get("is_open") == 1 and not store.get("is_close") and store.get("order_status") == 10


def rank_stores(
    stores: list[dict[str, Any]], origin: Origin | None, sort: SortBy, *, limit: int = 10
) -> list[dict[str, Any]]:
    rows = []
    for store in stores:
        avg, count = _rating(store)
        km = distance_km(origin, store) if origin else None
        rows.append({
            "store_id": str(store.get("id")),
            "name": str(store.get("name") or ""),
            "address": str(store.get("address") or ""),
            "distance_km": round(km, 1) if km is not None else None,
            "rating": avg,
            "rating_count": count,
            "score": round((avg * count + RATING_PRIOR * RATING_PRIOR_WEIGHT) / (count + RATING_PRIOR_WEIGHT), 3),
            "orderable": is_orderable(store),
        })

    def far(row: dict[str, Any]) -> float:
        return row["distance_km"] if row["distance_km"] is not None else math.inf

    if sort == "rating":
        if origin:
            rows = [row for row in rows if far(row) <= RATING_MAX_KM]
        rows.sort(key=lambda row: (not row["orderable"], -row["score"], far(row)))
    else:
        rows.sort(key=lambda row: (not row["orderable"], far(row), -row["score"]))
    return rows[:limit]
