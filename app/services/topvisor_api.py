import requests
import json
from app.config import settings


class TopvisorService:
    def __init__(self):
        self.base_url = "https://api.topvisor.com/v2/json"
        self.headers = {
            "Content-Type": "application/json",
            "User-Id": str(settings.TV_USER_ID),
            "Authorization": f"Bearer {settings.TV_API_KEY}",
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

        params = {"name": clean_name, "url": site_url}  # <--- БОЛЬШЕ НИКАКИХ "Check:"

        result = self._request("projects_2", "projects", "add", params)

        if isinstance(result, dict):
            return result.get("id")
        if isinstance(result, (str, int)):
            return result
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

        csv_lines = ["name;group_name"]  # Заголовок

        for item in keywords_data:
            # Экранируем точку с запятой в самом запросе, если вдруг есть
            clean_phrase = item["phrase"].replace(";", "")
            clean_group = item["group"].replace(";", "")

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

    def start_volume_checking(self, project_id: int, region_key: int = 225):
        """
        Запускает проверку частоты.
        region_key: 225 - это Россия (Yandex).
        """
        params = {
            "project_id": project_id,
            "searcher_key": 0,  # 0 - Яндекс
            "region_key": region_key,
        }

        # В V2 проверка частоты запускается через добавление задачи в Task Manager
        # Модуль: task_2, Метод: task, Команда: add
        # Параметры задачи определяют, что мы собираем volume

        # ПРИМЕЧАНИЕ: В Топвизоре специфичная логика. Чтобы снять частоту,
        # нужно создать задачу типа "Standard positions" или специальную volume задачу.
        # Однако, самый простой способ для v2 — вызвать volume collecting.

        # Пробуем через endpoint "проверка частоты": /add/snapshots_2/volume
        # Если его нет, идем через задачи. Самый надежный путь в V2 - tasks.

        task_params = {
            "project_id": project_id,
            "name": "Semyon_Volume_Check",  # Название задачи
            "type": "volume",  # Тип задачи: сбор частот
            "params": {
                "searcher_key": 0,  # Яндекс
                "region_key": region_key,  # Россия
                "engine": "yandex",  # Уточняем движок
            },
        }

        result = self._request("task_2", "task", "add", task_params)

        # Возвращает id задачи (task_id)
        return result.get("id")

    def get_task_status(self, task_id: int):
        """Проверяет состояние задачи по её ID"""
        params = {"id": task_id}
        result = self._request("task_2", "task", "get", params)

        # Если пришел список (бывает), берем первый элемент
        if isinstance(result, list) and result:
            result = result[0]

        # Нас интересует статус.
        # Возможные статусы: "process_wait", "process", "done", "error"
        return result.get("status_key")  # Вернет строку статуса

    def get_keywords_with_volume(self, project_id: int):
        """Получает список ключей и их частоту"""
        params = {
            "project_id": project_id,
            "fields": ["id", "name", "volume"],
            "show_volume": 1,
        }
        # get / keywords_2 / keywords
        return self._request("keywords_2", "keywords", "get", params)

    def delete_keywords(self, project_id: int, keyword_ids: list):
        """Удаляет ключи по ID"""
        if not keyword_ids:
            return

        params = {"project_id": project_id, "ids": keyword_ids}
        # del / keywords_2 / keywords
        return self._request("keywords_2", "keywords", "del", params)

    def search_region(self, query: str):
        """
        Ищет регион по названию.
        Используем V2 Filters, так как прямой параметр 'name' может вызывать ошибку 1003.
        """

        # Топвизор V2 просит передавать фильтры сложной структурой
        params = {
            "filters": [
                {
                    "name": "name",  # Поле, по которому ищем
                    "operator": "LIKE",  # Ищем совпадения (похоже на...)
                    "values": [f"%{query}%"],  # %Красно%
                },
                {
                    # Фильтруем только страны (0) и регионы (1, 2...)
                    # Чтобы не мусорить районами, можно убрать этот блок, если нужно всё подряд
                    "name": "country_id",
                    "operator": "NOT_EQUALS",
                    "values": ["0"],  # Пример фильтрации (не обязательно)
                },
            ],
            "fields": ["id", "name", "path", "type"],  # Явно просим вернуть эти поля
            "limit": 20,  # Не тащим всю базу, только топ-20 совпадений
        }

        # Вызываем: get / regions_2 / regions
        result = self._request("regions_2", "regions", "get", params)

        # Если API вернул список - обрабатываем
        if isinstance(result, list):
            output = []
            for item in result:
                # Формируем красивый ответ
                output.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "details": item.get("path", ""),  # "Россия.Сибирь..."
                    }
                )
            return output

        return []

    def setup_searcher(self, project_id: int, region_key: int, is_mobile: bool = True):
        """
        Добавляет поисковик (Яндекс) с указанием Региона и Устройства.

        region_key: ID города (например, 213)
        is_mobile: True = Mobile (Смартфон), False = Desktop
        """
        # В Топвизоре V2 (module projects_2, component searchers):
        # searcher_key: 0 - Яндекс, 1 - Google
        # device_key: 0 - PC, 1 - Tablet, 2 - Mobile (Phone) - точные ID зависят от ПС, но для Яндекса обычно:
        # device: 0 (Десктоп), 1 (Планшет), 2 (Мобильный)
        # *В некоторых версиях API параметр может называться 'type'.
        # Попробуем стандарт: device_key = 2 (Mobile).

        device_val = 2 if is_mobile else 0
        device_name = "Mobile" if is_mobile else "Desktop"

        params = {
            "project_id": project_id,
            "searcher_key": 0,  # Яндекс
            "region_key": region_key,
            "device_key": device_val,
            "enabled": 1,
        }

        # add / projects_2 / searchers
        print(
            f"Добавляем поиск: Яндекс | Регион: {region_key} | Устройство: {device_name} (key={device_val})"
        )

        result = self._request("projects_2", "searchers", "add", params)
        return result
