import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

SOURCE_CHAT_ID = os.getenv("SOURCE_CHAT_ID")

TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")
if not API_ID:
    raise ValueError("API_ID не найден в переменных окружения!")
if not API_HASH:
    raise ValueError("API_HASH не найден в переменных окружения!")
if not SOURCE_CHAT_ID:
    raise ValueError("SOURCE_CHAT_ID не найден в переменных окружения!")
if not TARGET_CHAT_ID:
    raise ValueError("TARGET_CHAT_ID не найден в переменных окружения!")

try:
    SOURCE_CHAT_ID = int(SOURCE_CHAT_ID)
    TARGET_CHAT_ID = int(TARGET_CHAT_ID)
except ValueError:
    raise ValueError("SOURCE_CHAT_ID и TARGET_CHAT_ID должны быть числами!")
