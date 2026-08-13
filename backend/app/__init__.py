"""При любом импорте пакета подхватываем API-ключи из backend/.env."""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # python-dotenv опционален — без него читаем только окружение
    pass
