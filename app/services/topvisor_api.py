import csv
import io
import json
from typing import Any, Dict, List, Optional

import requests
from app.config import settings


class TopvisorService:
    def __init__(self):
        self.base_url = "https://api.topvisor.com/v2/json"
        self.headers = {
            "Content-Type": "application/json",
            "User-Id": str(settings.TV_USER_ID),
            "Authorization": f"Bearer {settings.TV_API_KEY}",
        }

    # -----------------------------
    # Base request
    # -----------------------------
    def _request(
        self,
        module: str,
        method: str,
        command: str,
        params: Optional[dict] = None,
        *,
        expect_json: bool = True,
        timeout: int = 60,
    ):
        if params is None:
            params = {}

        url = f"{self.base_url}/{command}/{module}/{method}"

        try:
            response = requests.post(url, headers=self.headers, json=params, timeout=timeout)
            response.raise_for_status()

            if not expect_json:
                return response  # raw response (csv/file)

            data = response.json()

            # --- DEBUG ---
            # print(f"\nAPI REQ: {command}/{module}/{method}")
            # print(f"DATA: {json.dumps(data, ensure_ascii=False)[:2000]}")
            # -------------

            if isinstance(data, dict) and data.get("errors"):
                raise Exception(f"Topvisor API Error: {str(data['errors'])}")

            return data.get("result", data)

        except Exception as e:
            print(f"CRITICAL API ERROR: {str(e)} | endpoint={command}/{module}/{method}")
            raise

    # -----------------------------
    # Projects / Keywords
    # -----------------------------
    def create_project(self, site_url: str):
        clean_name = site_url.replace("https://", "").replace("http://", "").strip("/")
        params = {"name": clean_name, "url": site_url}
        result = self._request("projects_2", "projects", "add", params)

        if isinstance(result, dict):
            return result.get("id")
        if isinstance(result, (str, int)):
            return result
        if isinstance(result, list) and result:
            return result[0].get("id") if isinstance(result[0], dict) else result[0]

        raise Exception(f"Error creating project: {result}")

    def add_keywords(self, project_id: int, keywords_data: list) -> int:
        """
        keywords_data: список словарей [{'phrase': '...', 'group': '...'}]
        """
        if not keywords_data:
            return 0

        print(f"Импорт {len(keywords_data)} ключей с группами...")

        csv_lines = ["name;group_name"]  # header
        for item in keywords_data:
            phrase = str(item.get("phrase", "")).replace(";", "").strip()
            group = str(item.get("group", "General")).replace(";", "").strip() or "General"
            if not phrase:
                continue
            csv_lines.append(f"{phrase};{group}")

        csv_content = "\n".join(csv_lines)

        params = {"project_id": project_id, "keywords": csv_content}
        result = self._request("keywords_2", "keywords/import", "add", params)

        if isinstance(result, dict):
            added = int(result.get("countAdded", 0) or 0)
            print(f"Импортировано: {added}")
            return added

        return len(keywords_data)

    # -----------------------------
    # OLD (оставил для совместимости, но для позиций НЕ использовать)
    # -----------------------------
    def setup_searcher(self, project_id: int, region_key: int, is_mobile: bool = True):
        """
        ⚠️ УСТАРЕВШЕЕ: это projects_2/searchers.
        Для проверки позиций нужен positions_2 (см. ensure_yandex_mobile_region).
        """
        device_val = 2 if is_mobile else 0
        device_name = "Mobile" if is_mobile else "Desktop"

        params = {
            "project_id": project_id,
            "searcher_key": 0,  # Yandex
            "region_key": region_key,
            "device_key": device_val,
            "enabled": 1,
        }

        print(
            f"[DEPRECATED] projects_2/searchers: Яндекс | Регион: {region_key} | {device_name}"
        )

        return self._request("projects_2", "searchers", "add", params)

    # -----------------------------
    # Regions search (как у тебя было)
    # -----------------------------
    def search_region(self, query: str):
        params = {
            "filters": [
                {"name": "name", "operator": "LIKE", "values": [f"%{query}%"]},
                {"name": "country_id", "operator": "NOT_EQUALS", "values": ["0"]},
            ],
            "fields": ["id", "name", "path", "type"],
            "limit": 20,
        }

        result = self._request("regions_2", "regions", "get", params)

        if isinstance(result, list):
            return [
                {"id": item.get("id"), "name": item.get("name"), "details": item.get("path", "")}
                for item in result
            ]
        return []

    # -----------------------------
    # Positions RankTracker: Yandex + Region + Mobile + one-time check
    # -----------------------------
    @staticmethod
    def _decode_bytes(raw: bytes) -> str:
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _parse_csv_any_delim(text: str) -> List[List[str]]:
        candidates = [";", "\t", ","]
        best_rows: List[List[str]] = []
        best_cols = -1

        for delim in candidates:
            try:
                rows = []
                reader = csv.reader(io.StringIO(text), delimiter=delim)
                for r in reader:
                    if any((c or "").strip() for c in r):
                        rows.append(r)
                cols = max((len(r) for r in rows[:10]), default=0)
                if cols > best_cols:
                    best_cols = cols
                    best_rows = rows
            except Exception:
                continue

        return best_rows

    def ensure_yandex_mobile_region(
        self,
        project_id: int,
        region_key: int,
        *,
        depth: int = 1,
        lang: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Делает:
        1) add/positions_2/searchers  (Яндекс)
        2) add/positions_2/searchers_regions (регион + phone + depth)
        3) вычисляет regions_index для checker/go
        """
        # 1) add searcher
        try:
            self._request("positions_2", "searchers", "add", {"project_id": project_id, "searcher_key": 0})
            searcher_added = True
        except Exception as e:
            print(f"Topvisor: searcher add skipped/failed (maybe exists): {e}")
            searcher_added = False

        # 2) add region (device=phone)
        params = {
            "project_id": project_id,
            "searcher_key": 0,
            "region_key": int(region_key),
            "region_device": 2,     # 0 desktop, 1 tablet, 2 phone
            "region_depth": int(depth),
        }
        if lang:
            params["region_lang"] = lang

        try:
            self._request("positions_2", "searchers_regions", "add", params)
            region_added = True
        except Exception as e:
            print(f"Topvisor: region add skipped/failed (maybe exists): {e}")
            region_added = False

        region_index = self._resolve_region_index_from_export(
            project_id=project_id,
            searcher_key=0,
            region_key=int(region_key),
            device=2,
            depth=int(depth),
        )

        return {
            "searcher_added": searcher_added,
            "region_added": region_added,
            "region_index": region_index,
            "searcher_key": 0,
            "region_key": int(region_key),
            "region_device": 2,
            "region_depth": int(depth),
        }

    def _export_searchers_regions_csv(self, project_id: int) -> str:
        resp = self._request(
            "positions_2",
            "searchers_regions/export",
            "get",
            {"project_id": project_id},
            expect_json=False,
            timeout=60,
        )
        return self._decode_bytes(resp.content or b"")

    def _resolve_region_index_from_export(
        self,
        *,
        project_id: int,
        searcher_key: int,
        region_key: int,
        device: int,
        depth: int,
    ) -> int:
        """
        regions_indexes для checker/go — это "индексы регионов" в проекте.
        Самый надёжный путь — взять export и найти нужную строку.
        """
        try:
            csv_text = self._export_searchers_regions_csv(project_id)
            rows = self._parse_csv_any_delim(csv_text)
            if not rows:
                return 0

            # если есть хедер — пропускаем
            def _is_int(x: str) -> bool:
                try:
                    int(str(x).strip())
                    return True
                except Exception:
                    return False

            start = 0
            if rows and (not _is_int(rows[0][0] if rows[0] else "")):
                start = 1

            data_rows = rows[start:]
            for idx, cols in enumerate(data_rows):
                # expected columns:
                # 0 searcher_key, 1 region_key, 4 device, 5 depth
                c0 = cols[0].strip() if len(cols) > 0 else ""
                c1 = cols[1].strip() if len(cols) > 1 else ""
                c4 = cols[4].strip() if len(cols) > 4 else ""
                c5 = cols[5].strip() if len(cols) > 5 else ""

                if not c0 or not c1:
                    continue

                try:
                    row_searcher = int(c0)
                    row_region = int(c1)
                    row_device = int(c4 or 0)
                    row_depth = int(c5 or 1)
                except Exception:
                    continue

                if (
                    row_searcher == int(searcher_key)
                    and row_region == int(region_key)
                    and row_device == int(device)
                    and row_depth == int(depth)
                ):
                    return idx  # 0-based

            return 0
        except Exception as e:
            print(f"Topvisor: failed to resolve region_index, fallback=0 | {e}")
            return 0

    def check_positions_once(self, project_id: int, *, region_index: int, do_snapshots: int = 0) -> Dict[str, Any]:
        """
        edit/positions_2/checker/go — запускает разовую проверку позиций.
        """
        params = {
            "filters": [{"name": "id", "operator": "EQUALS", "values": [int(project_id)]}],
            "regions_indexes": [int(region_index)],
            "do_snapshots": int(do_snapshots),
        }

        # 1) пробуем 0-based
        try:
            result = self._request("positions_2", "checker/go", "edit", params)
            return {"ok": True, "region_index_used": int(region_index), "result": result}
        except Exception as e:
            # 2) fallback на 1-based (иногда встречается на аккаунтах/проектах)
            try:
                params["regions_indexes"] = [int(region_index) + 1]
                result = self._request("positions_2", "checker/go", "edit", params)
                return {"ok": True, "region_index_used": int(region_index) + 1, "result": result, "fallback": "1-based"}
            except Exception:
                return {"ok": False, "error": str(e), "region_index_tried": int(region_index)}

    def setup_yandex_mobile_region_and_get_index(self, project_id: int, region_key: int, depth: int = 1) -> int:
        """
        1) Добавляет Яндекс в Rank Tracker (positions_2/searchers)
        2) Добавляет регион+устройство phone (positions_2/searchers_regions)
        3) Возвращает regions_index из get/projects_2/projects (show_searchers_and_regions)
        """
        # 1) add searcher (Yandex = 0)
        try:
            self._request("positions_2", "searchers", "add", {"project_id": project_id, "searcher_key": 0})
        except Exception as e:
            # если уже есть — не критично
            print(f"Topvisor: searcher add skipped/failed (maybe exists): {e}")

        # 2) add region for phone
        try:
            self._request(
                "positions_2",
                "searchers_regions",
                "add",
                {
                    "project_id": project_id,
                    "searcher_key": 0,
                    "region_key": int(region_key),
                    "region_device": 2,     # 0 desktop, 1 tablet, 2 phone
                    "region_depth": int(depth),
                },
            )
        except Exception as e:
            print(f"Topvisor: region add skipped/failed (maybe exists): {e}")

        # 3) resolve regions_index (самый правильный способ)
        idx = self.get_regions_index(project_id, searcher_key=0, region_key=int(region_key), device=2)
        return int(idx)

    def get_regions_index(self, project_id: int, searcher_key: int, region_key: int, device: int = 2) -> int:
        """
        Берём index из get/projects_2/projects при show_searchers_and_regions=2.
        Это тот самый regions_indexes, который нужен для checker/go.
        """
        params = {
            "limit": 1,
            "show_searchers_and_regions": 2,
            "filters": [{"name": "id", "operator": "EQUALS", "values": [str(project_id)]}],
        }
        proj_list = self._request("projects_2", "projects", "get", params)
        if not isinstance(proj_list, list) or not proj_list:
            return 0

        proj = proj_list[0] if isinstance(proj_list[0], dict) else {}
        # Варианты структуры могут отличаться, поэтому делаем “мягкий” обход
        searchers = proj.get("searchers") or proj.get("positions_searchers") or []

        def _as_int(x, default=0):
            try:
                return int(str(x).strip())
            except Exception:
                return default

        for s in searchers:
            if not isinstance(s, dict):
                continue
            if _as_int(s.get("searcher_key", s.get("key"))) != _as_int(searcher_key):
                continue

            regions = s.get("regions") or s.get("searchers_regions") or s.get("locations") or []
            for r in regions:
                if not isinstance(r, dict):
                    continue

                r_key = _as_int(r.get("region_key", r.get("key", r.get("id"))))
                r_dev = _as_int(r.get("region_device", r.get("device", r.get("device_key"))))
                r_idx = _as_int(r.get("index"))

                if r_key == _as_int(region_key) and r_dev == _as_int(device) and r_idx is not None:
                    return r_idx

        return 0


    def check_positions_once(self, project_id: int, region_index: int, do_snapshots: int = 0):
        """
        Запускает разовую проверку позиций.
        Ожидаемый ответ: {"projectsIds":[...]}
        """
        params = {
            "filters": [{"name": "id", "operator": "EQUALS", "values": [str(project_id)]}],
            "regions_indexes": [int(region_index)],
            "do_snapshots": int(do_snapshots),
        }
        return self._request("positions_2", "checker/go", "edit", params)


    def get_positions_percent(self, project_id: int):
        """
        Удобно дебажить: если проверка стартанула, positions_percent начнёт меняться.
        """
        params = {
            "limit": 1,
            "fields": ["positions_percent"],
            "filters": [{"name": "id", "operator": "EQUALS", "values": [str(project_id)]}],
        }
        res = self._request("projects_2", "projects", "get", params)
        if isinstance(res, list) and res and isinstance(res[0], dict):
            return res[0].get("positions_percent")
        return None
