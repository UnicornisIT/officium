# Пакет модульных тестов
import sys
from pathlib import Path

# Общие fixtures импортируются по имени и при discover из корня проекта.
_tests_path = str(Path(__file__).resolve().parent)
if _tests_path not in sys.path:
    sys.path.insert(0, _tests_path)
