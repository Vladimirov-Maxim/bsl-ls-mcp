"""Dev/bundle launcher MCP-сервера. После `pip install -e .` есть консольная
команда `bsl-ls-mcp`; этот скрипт нужен для запуска без установки (добавляет src
в путь) и как entry-point для PyInstaller. Вся логика — в bsl_ls_mcp.cli.main."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bsl_ls_mcp.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
