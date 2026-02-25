"""
تنظیمات مرکزی پروژه
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# GitHub
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_API_BASE: str = "https://api.github.com"
GITHUB_HEADERS: dict = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Gitea
GITEA_URL: str = os.getenv("GITEA_URL", "http://localhost:3000")
GITEA_TOKEN: str = os.getenv("GITEA_TOKEN", "")
GITEA_API_BASE: str = f"{GITEA_URL}/api/v1"
GITEA_HEADERS: dict = {
    "Authorization": f"token {GITEA_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
# ── Organization مقصد در Gitea ──
GITEA_ORG: str = os.getenv("GITEA_ORG", "github-mirror")

# Search
SEARCH_KEYWORDS: list[str] = [
    kw.strip()
    for kw in os.getenv("SEARCH_KEYWORDS", "python").split(",")
    if kw.strip()
]
SEARCH_LANGUAGE: str = os.getenv("SEARCH_LANGUAGE", "python")
MIN_STARS: int = int(os.getenv("MIN_STARS", "10"))
PROJECTS_PER_KEYWORD: int = int(os.getenv("PROJECTS_PER_KEYWORD", "20"))
MAX_SCAN_PER_KEYWORD: int = int(os.getenv("MAX_SCAN_PER_KEYWORD", "150"))

# شرایط اجباری
MIN_ISSUES_REQUIRED: int = int(os.getenv("MIN_ISSUES_REQUIRED", "3"))
MIN_PRS_REQUIRED: int = int(os.getenv("MIN_PRS_REQUIRED", "2"))
MIN_CODE_FILES_REQUIRED: int = int(os.getenv("MIN_CODE_FILES_REQUIRED", "3"))

# حداکثر استخراج
MAX_ISSUES_EXTRACT: int = int(os.getenv("MAX_ISSUES_EXTRACT", "50"))
MAX_PRS_EXTRACT: int = int(os.getenv("MAX_PRS_EXTRACT", "30"))
MAX_CODE_FILES_EXTRACT: int = int(os.getenv("MAX_CODE_FILES_EXTRACT", "25"))

CODE_EXTENSIONS: tuple[str, ...] = tuple(
    ext.strip()
    for ext in os.getenv("CODE_EXTENSIONS", ".py,.js,.ts,.go,.rs,.java").split(",")
    if ext.strip()
)

# زمان‌بندی
CRON_INTERVAL_HOURS: int = int(os.getenv("CRON_INTERVAL_HOURS", "6"))

# لاگ و دیتابیس
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: str = os.getenv("LOG_FILE", str(DATA_DIR / "crawler.log"))
DB_PATH: str = os.getenv("DB_PATH", str(DATA_DIR / "repositories.db"))
