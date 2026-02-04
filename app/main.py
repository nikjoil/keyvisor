from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from app.services.topvisor_api import TopvisorService
from app.utils.excel_parser import parse_excel_file

app = FastAPI(title="KeyVisor API")

@app.get("/")
def health_check():
    return {"status": "ok"}

# Тестовая ручка создания (оставь для отладки)
@app.get("/test-create-project")
def test_create_project(url: str = "google.com"):
    try:
        service = TopvisorService()
        pid = service.create_project(f"https://{url}")
        return {"status": "success", "id": pid}
    except Exception as e:
        return {"status": "error", "details": str(e)}

# === ГЛАВНАЯ РУЧКА: ЗАГРУЗКА EXCEL И СОЗДАНИЕ ПРОЕКТА ===
@app.post("/upload-task")
async def upload_task(
    url: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        print(f"--- ЗАПУСК ЗАДАЧИ ДЛЯ {url} ---")

        # 1. Читаем Excel (теперь он вернет Query + Group)
        content = await file.read()
        parsed_data = parse_excel_file(content)

        if not parsed_data:
            raise HTTPException(status_code=400, detail="Файл пуст или некорректен (нужны 2 столбца)")

        print(f"Парсинг завершен. Готово к загрузке: {len(parsed_data)} строк")

        # 2. Сервис
        service = TopvisorService()

        # 3. Создаем проект (Имя теперь будет просто site.com)
        project_id = service.create_project(url)
        print(f"Проект создан. ID: {project_id}")

        # 4. Заливаем CSV
        count = service.add_keywords(project_id, parsed_data)

        return {
            "status": "success",
            "message": f"Проект '{url}' создан.",
            "project_id": project_id,
            "uploaded_keywords": count,
            "groups_detected": len(set(item['group'] for item in parsed_data)) # Для интереса вернем кол-во найденных групп
        }

    except Exception as e:
        print(f"GLOBAL ERROR: {str(e)}")
        return {"status": "error", "details": str(e)}
