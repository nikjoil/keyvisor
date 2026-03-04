import asyncio
import datetime as dt
import re
import requests
from app.config import settings


class WordKeeperService:
    """
    Единственный формат запросов (super exact):
      "[!слово1 !слово2]"

    keep_project=False по умолчанию — чтобы не забивать лимит проектов.
    Если keep_project=True — проект ОСТАНЕТСЯ в аккаунте WordKeeper и будет виден в "Моих проектах".
    """

    def __init__(self):
        self.base_url = "https://word-keeper.ru/api"
        self.token = settings.WK_TOKEN

        # мета для дебага (можно отдавать в API ответе)
        self.last_project_id = None
        self.last_project_name = None

        self.token = (settings.WK_TOKEN or "").strip()
        if not self.token:
            raise ValueError("WORDKEEPER_TOKEN is empty after strip()")

    def _post(self, method: str, data: dict):
        data["token"] = self.token
        url = f"{self.base_url}/{method}"
        resp = requests.post(url, json=data, timeout=60)
        try:
            return resp.json()
        except Exception as e:
            print(f"WK JSON Decode Error: {resp.text}")
            raise e

    def _flatten_keywords(self, keywords: list) -> list[str]:
        """
        Превращаем вход в плоский список фраз.
        Режем по \r \n , ; |
        """
        out: list[str] = []
        for kw in keywords or []:
            if kw is None:
                continue
            s = str(kw).strip()
            if not s:
                continue
            parts = re.split(r"[\r\n,;|]+", s)
            out.extend([p.strip() for p in parts if p.strip()])

        # дедуп с сохранением порядка
        return list(dict.fromkeys(out))

    def _prepare_keywords_super_exact(self, keywords: list[str]) -> dict[str, str]:
        """
        Делает mapping:
          оригинал: "аренда авто"
          формат:   "[!аренда !авто]"  (в кавычках)

        Возвращает: { '"[!аренда !авто]"': 'аренда авто', ... }
        """
        mapping: dict[str, str] = {}

        for kw in keywords:
            clean = kw.strip()
            if not clean:
                continue

            words = clean.split()
            inner = "[!" + " !".join(words) + "]"
            processed = f'"{inner}"'  # ✅ суперточно: кавычки вокруг [!...]

            mapping[processed] = clean

        return mapping

    async def check_volume(
        self,
        keywords: list,
        region_id: int = 213,
        *,
        keep_project: bool = False,
        with_meta: bool = False,
        project_name: str | None = None,
    ):
        """
        Создание -> ожидание -> результат.

        Возвращает:
          - with_meta=False: dict { 'фраза': частота }
          - with_meta=True:  { 'results': {...}, 'meta': {...} }
        """
        keywords = self._flatten_keywords(keywords)
        if not keywords:
            return {"results": {}, "meta": {}} if with_meta else {}

        kw_map = self._prepare_keywords_super_exact(keywords)
        formatted_list = list(kw_map.keys())
        text_data = "\n".join(formatted_list)

        # Имя проекта — чтобы легко найти в аккаунте WK
        if not project_name:
            ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            project_name = f"KV API | geo={region_id} | n={len(keywords)} | {ts}"

        self.last_project_name = project_name

        # ✅ Лёгкий “дебаг без мусора”: показываем первые 3 строки, как реально отправили в WK
        print("WK DEBUG payload sample:", formatted_list[:3])

        print(
            f"WK: Запуск проверки частоты для {len(keywords)} фраз (Регион {region_id})..."
        )

        create_params = {
            "text": text_data,
            "geo": region_id,
            "name": project_name,
        }
        project_resp = self._post("create_freqDiff", create_params)

        if project_resp.get("status") != "ok":
            raise Exception(f"WK Error creating project: {project_resp}")

        project_id = project_resp.get("id")
        self.last_project_id = project_id

        print(
            f"WK: Проект {project_id} создан. Ждем результатов... keep_project={keep_project}"
        )

        # polling
        for _ in range(30):
            await asyncio.sleep(3)

            check_resp = self._post("get_result", {"id": project_id})
            status = check_resp.get("status")

            if status == "ok":
                raw_results = check_resp.get("results", {})  # { formatted_kw: volume }

                # ✅ Не удаляем проект, если keep_project=True
                if not keep_project:
                    try:
                        self._post("remove", {"id": project_id})
                    except Exception as _e:
                        print(f"WK: remove failed for project_id={project_id}: {_e}")

                final_results: dict[str, int] = {}
                for fmt_kw, volume in raw_results.items():
                    original_kw = kw_map.get(fmt_kw)
                    if original_kw:
                        final_results[original_kw] = int(volume) if volume else 0

                if with_meta:
                    return {
                        "results": final_results,
                        "meta": {
                            "project_id": project_id,
                            "project_name": project_name,
                            "keep_project": keep_project,
                            "requested": len(keywords),
                            "returned": len(final_results),
                            "query_format": "QUOTES_AROUND_BANG_BRACKETS",  # фиксируем в мета
                        },
                    }

                return final_results

            if status == "error":
                raise Exception(f"WK вернул ошибку: {check_resp.get('error')}")

        raise TimeoutError("WK не успел обработать запросы за разумное время.")

    def remove_project(self, project_id: int | str):
        """Ручное удаление проекта WK (если включал keep_project=True)."""
        return self._post("remove", {"id": project_id})
