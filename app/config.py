import os
from pathlib import Path
from dotenv import load_dotenv

# Грузим .env стабильно (не зависит от того, откуда запущен uvicorn)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)


class Settings:
    TV_USER_ID = (os.getenv("TV_USER_ID") or "").strip()
    TV_API_KEY = (os.getenv("TV_API_KEY") or "").strip()

    # ВАЖНО: режем пробелы/переносы
    WK_TOKEN = (os.getenv("WORDKEEPER_TOKEN") or "").strip()
    KS_TOKEN = (os.getenv("KEYSSO_TOKEN") or "").strip()

    def validate(self):
        if not self.TV_USER_ID or not self.TV_API_KEY:
            raise ValueError("❌ MISSING TOPVISOR CONFIG IN .ENV")
        if not self.WK_TOKEN:
            raise ValueError("❌ MISSING WORDKEEPER_TOKEN IN .ENV (or it is empty/whitespace)")
        if not self.KS_TOKEN:
            raise ValueError("❌ MISSING KEYSSO_TOKEN IN .ENV")


settings = Settings()
settings.validate()