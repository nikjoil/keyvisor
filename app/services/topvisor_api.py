import requests
import json
from app.config import settings

class TopvisorService:
    def __init__(self):
        self.base_url = "https://api.topvisor.com/v2/json"
        self.headers = {
            "Content-Type": "application/json",
            "User-Id": str(settings.TV_USER_ID),
            "Authorization": f"Bearer {settings.TV_API_KEY}"
        }

    def _request(self, module: str, method: str, command: str, params: dict = None):
        if params is None:
            params = {}

        url = f"{self.base_url}/{command}/{module}/{method}"

        try:
            response = requests.post(url, headers=self.headers, json=params)
            response.raise_for_status()

            data = response.json()

            # --- DEBUG ---
            # print(f"\nAPI REQ: {command}/{module}/{method}")
            # print(f"DATA: {data}")
            # -------------

            if "errors" in data and data["errors"]:
                raise Exception(f"Topvisor API Error: {str(data['errors'])}")

            return data.get("result", data)

        except Exception as e:
            print(f"CRITICAL API ERROR: {str(e)}")
            raise e

    def create_project(self, site_url: str):
        # Чистим URL от http/https для красивого имени
        clean_name = site_url.replace("https://", "").replace("http://", "").strip("/")

        params = {
            "name": clean_name,  # <--- БОЛЬШЕ НИКАКИХ "Check:"
            "url": site_url
        }

        result = self._request("projects_2", "projects", "add", params)

        if isinstance(result, dict): return result.get("id")
        if isinstance(result, (str, int)): return result
        if isinstance(result, list) and result:
            return result[0].get("id") if isinstance(result[0], dict) else result[0]

        raise Exception(f"Error creating project: {result}")

    def add_keywords(self, project_id: int, keywords_data: list):
        """
        keywords_data: список словарей [{'phrase': '...', 'group': '...'}]
        """
        if not keywords_data:
            return 0

        print(f"Импорт {len(keywords_data)} ключей с группами...")

        # Формируем CSV для импорта
        # В документации поля: name (запрос), group_name (имя группы/папки)
        # В качестве разделителя используем точку с запятой ';', это надежнее запятой

        csv_lines = ["name;group_name"] # Заголовок

        for item in keywords_data:
            # Экранируем точку с запятой в самом запросе, если вдруг есть
            clean_phrase = item['phrase'].replace(";", "")
            clean_group = item['group'].replace(";", "")

            line = f"{clean_phrase};{clean_group}"
            csv_lines.append(line)

        # Склеиваем всё в одну строку
        csv_content = "\n".join(csv_lines)

        params = {
            "project_id": project_id,
            "keywords": csv_content,
        }

        # Отправляем
        result = self._request("keywords_2", "keywords/import", "add", params)

        if isinstance(result, dict):
             added = result.get("countAdded", 0)
             print(f"Импортировано: {added}")
             return added

        return len(keywords_data)
