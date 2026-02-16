import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    TV_USER_ID = os.getenv("TV_USER_ID")
    TV_API_KEY = os.getenv("TV_API_KEY")
    WK_TOKEN = os.getenv("WORDKEEPER_TOKEN")
    KS_TOKEN = os.getenv("KEYSSO_TOKEN")

    def validate(self):
        if not self.TV_USER_ID or not self.TV_API_KEY:
            raise ValueError("❌ MISSING TOPVISOR CONFIG IN .ENV")
        if not self.WK_TOKEN:
            raise ValueError("❌ MISSING WORDKEEPER_TOKEN IN .ENV")
        if not self.KS_TOKEN:
            raise ValueError("❌ MISSING KEYSSO_TOKEN IN .ENV")


settings = Settings()
settings.validate()  # Проверяем сразу при запуске
