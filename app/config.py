import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    TV_USER_ID = os.getenv("TV_USER_ID")
    TV_API_KEY = os.getenv("TV_API_KEY")

    if not TV_USER_ID or not TV_API_KEY:
        raise ValueError("В файле .env не заданы TV_USER_ID или TV_API_KEY!")

settings = Settings()
