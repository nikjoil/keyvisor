import json
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.config import settings


class KeysSoService:
    """Keys.so API wrapper.

    Основная ручка тут: GET /report/simple/organic/keywords

    Важно по метрикам:
    - superwsk (если есть) — приоритетная метрика для фильтрации (и сортировки)
    - если superwsk нет -> wsk
    - если wsk нет -> ws

    Пагинация: current_page / last_page / per_page.
    """

    DEFAULT_PER_PAGE_CAP = 1000
    DEFAULT_FALLBACK_LOG_SAMPLES = 20

    def __init__(self):
        self.base_url = "https://api.keys.so"

        # Для GET Content-Type не нужен. Лучше попросить JSON и контролировать компрессию.
        self.headers = {
            "X-Keyso-TOKEN": settings.KS_TOKEN,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept": "application/json",
            # Если в окружении/прокси проблемы с brotli — просим gzip/deflate.
            "Accept-Encoding": "gzip, deflate",
        }

    def _safe_json(self, response: requests.Response) -> Optional[Any]:
        """Парсит JSON максимально устойчиво.

        Иногда Keys.so/прокси может вернуть:
        - brotli (`Content-Encoding: br`)
        - HTML/пустой ответ при блокировках

        Возвращает Python-объект или None.
        """
        try:
            return response.json()
        except Exception:
            raw = response.content or b""
            enc = (response.headers.get("Content-Encoding") or "").lower()
            ctype = (response.headers.get("Content-Type") or "").lower()

            print(
                "Keys.so DEBUG: JSON decode failed | "
                f"status={response.status_code} | content-type={ctype} | content-encoding={enc} | "
                f"len={len(raw)} | head={raw[:120]!r}"
            )

            if not raw:
                return None

            if enc == "br":
                try:
                    import brotli  # type: ignore

                    raw = brotli.decompress(raw)
                except Exception as e:
                    print(f"Keys.so DEBUG: brotli decompress failed: {e}")
                    return None

            text = raw.decode("utf-8", errors="replace")
            try:
                return json.loads(text)
            except Exception as e:
                print(f"Keys.so DEBUG: json.loads failed: {e} | head={text[:200]!r}")
                return None

    @staticmethod
    def _clean_domain(domain: str) -> str:
        clean_domain = (
            domain.replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
            .strip("/")
        )
        return clean_domain.split("/")[0]

    @staticmethod
    def _pick_freq(item: Dict[str, Any]) -> Tuple[int, str]:
        """Возвращает (freq_int, metric_used)."""
        metric = "none"
        freq = 0

        if item.get("superwsk") is not None:
            metric = "superwsk"
            freq = item.get("superwsk")
        elif item.get("wsk") is not None:
            metric = "wsk"
            freq = item.get("wsk")
        elif item.get("ws") is not None:
            metric = "ws"
            freq = item.get("ws")

        try:
            return int(freq) if freq is not None else 0, metric
        except Exception:
            return 0, metric

    def get_site_organic(
        self,
        domain: str,
        base: str = "msk",
        min_freq: int = 2,
        limit: int = 1000,
        per_page_cap: int = DEFAULT_PER_PAGE_CAP,
        max_pages: Optional[int] = None,
        max_items: Optional[int] = None,
        fallback_log_samples: int = DEFAULT_FALLBACK_LOG_SAMPLES,
    ) -> List[Dict[str, Any]]:
        """Тянет органические ключи Keys.so по ВСЕМ страницам.

        Параметры:
        - limit: используется как per_page (backward compatible с текущими вызовами)
        - per_page_cap: жесткий кап per_page (на случай лимитов API)
        - max_pages / max_items: предохранители
        - fallback_log_samples: сколько примеров fallback'ов (wsk/ws) печатать

        Возвращаем список объектов в формате проекта:
        {phrase, group, initial_exact_freq}
        """

        clean_domain = self._clean_domain(domain)

        # per_page cap
        try:
            per_page = int(limit)
        except Exception:
            per_page = 1000

        if per_page <= 0:
            per_page = 1000

        if per_page_cap and per_page > per_page_cap:
            print(f"Keys.so: per_page capped {per_page} -> {per_page_cap}")
            per_page = per_page_cap

        url = f"{self.base_url}/report/simple/organic/keywords"

        page = 1
        collected: List[Dict[str, Any]] = []

        metric_used_cnt = {"superwsk": 0, "wsk": 0, "ws": 0, "none": 0}
        fallback_logged = 0

        while True:
            params = {
                "domain": clean_domain,
                "base": base,
                "per_page": per_page,
                "page": page,
                # В приоритете superwsk (как ты просил)
                "sort": "superwsk|desc",
            }

            print(
                f"Keys.so: GET {url} | domain={clean_domain} | base={base} | page={page} | per_page={per_page}"
            )

            try:
                response = requests.get(
                    url, headers=self.headers, params=params, timeout=30
                )
            except Exception as e:
                print(f"Keys.so EXCEPTION: request failed: {e}")
                break

            if response.status_code != 200:
                print(
                    f"CRITICAL API ERROR {response.status_code}. RAW BODY:\n"
                    f"{(response.text or '')[:800]}"
                )
                break

            payload = self._safe_json(response)
            if payload is None:
                print("CRITICAL JSON ERROR. EMPTY/UNPARSABLE BODY.")
                break

            # Иногда встречается list с одним dict
            if isinstance(payload, list):
                payload = payload[0] if payload else {}

            if not isinstance(payload, dict):
                print(f"Keys.so DEBUG: unexpected payload type: {type(payload)}")
                break

            items = payload.get("data", []) or []
            try:
                last_page = int(payload.get("last_page", page) or page)
            except Exception:
                last_page = page

            if page == 1 and items:
                try:
                    print(
                        "Keys.so DEBUG sample item:",
                        json.dumps(items[0], ensure_ascii=False)[:400],
                    )
                except Exception:
                    pass

            for item in items:
                if not isinstance(item, dict):
                    continue

                kw = item.get("word") or item.get("keyword")
                if not kw:
                    continue

                freq_int, metric = self._pick_freq(item)
                metric_used_cnt[metric] += 1

                # Логи про то, какая метрика реально использовалась
                if metric == "superwsk":
                    if fallback_logged < fallback_log_samples:
                        print(
                            f"Keys.so metric used: phrase='{kw}' -> superwsk={freq_int}"
                        )
                        fallback_logged += 1
                elif metric == "wsk":
                    if fallback_logged < fallback_log_samples:
                        print(
                            f"Keys.so metric used: phrase='{kw}' -> wsk={freq_int} (superwsk missing)"
                        )
                        fallback_logged += 1
                elif metric == "ws":
                    if fallback_logged < fallback_log_samples:
                        print(
                            f"Keys.so metric used: phrase='{kw}' -> ws={freq_int} (superwsk+wsk missing)"
                        )
                        fallback_logged += 1
                else:
                    if fallback_logged < fallback_log_samples:
                        print(
                            f"Keys.so metric used: phrase='{kw}' -> none (no superwsk/wsk/ws)"
                        )
                        fallback_logged += 1

                if freq_int >= min_freq:
                    collected.append(
                        {
                            "phrase": kw,
                            "group": f"[Keys.so] {clean_domain}",
                            "initial_exact_freq": freq_int,
                        }
                    )

                    if max_items and len(collected) >= max_items:
                        print(f"Keys.so: reached max_items={max_items}, stop.")
                        break

            if max_items and len(collected) >= max_items:
                break

            if max_pages and page >= max_pages:
                print(f"Keys.so: reached max_pages={max_pages}, stop.")
                break

            if page >= last_page:
                break

            page += 1

        print(
            "Keys.so metric usage summary: "
            f"superwsk={metric_used_cnt['superwsk']}, "
            f"wsk={metric_used_cnt['wsk']}, "
            f"ws={metric_used_cnt['ws']}, "
            f"none={metric_used_cnt['none']} | "
            f"passed={len(collected)} (freq >= {min_freq})"
        )

        return collected
