"""
سیستم لاگ‌گذاری مرکزی
"""

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from config.settings import LOG_LEVEL, LOG_FILE

console = Console()


def setup_logger(name: str = "github_crawler") -> logging.Logger:
    """ساخت و پیکربندی لاگر"""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # هندلر ترمینال
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    rich_handler.setLevel(logging.DEBUG)
    rich_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))

    # هندلر فایل
    log_path = Path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    logger.addHandler(rich_handler)
    logger.addHandler(file_handler)
    return logger


log = setup_logger()