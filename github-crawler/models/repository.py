"""
مدل‌های داده و لایه دیتابیس
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from config.settings import DB_PATH
from utils.logger import log


# ──────────────────────────────────────────────
# مدل‌های Pydantic
# ──────────────────────────────────────────────

class RepositoryInfo(BaseModel):
    """اطلاعات یک مخزن گیت‌هاب"""

    full_name: str
    owner: str
    name: str
    description: str | None = None
    html_url: str
    clone_url: str
    language: str | None = None
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    default_branch: str = "main"
    created_at: str | None = None
    updated_at: str | None = None
    topics: list[str] = Field(default_factory=list)

    # ── فیلدهای اعتبارسنجی داده آموزشی ──
    has_readme: bool = False
    has_sufficient_issues: bool = False
    has_sufficient_prs: bool = False
    has_sufficient_code: bool = False
    is_training_ready: bool = False  # همه شرایط برقرار است

    migrated: bool = False
    discovered_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    rejection_reason: str | None = None  # دلیل رد شدن

    @classmethod
    def from_github_api(cls, data: dict) -> RepositoryInfo:
        """ساخت مدل از پاسخ API گیت‌هاب"""
        owner_data = data.get("owner", {})
        return cls(
            full_name=data.get("full_name", ""),
            owner=owner_data.get("login", ""),
            name=data.get("name", ""),
            description=data.get("description"),
            html_url=data.get("html_url", ""),
            clone_url=data.get("clone_url", ""),
            language=data.get("language"),
            stars=data.get("stargazers_count", 0),
            forks=data.get("forks_count", 0),
            open_issues=data.get("open_issues_count", 0),
            default_branch=data.get("default_branch", "main"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            topics=data.get("topics", []),
        )

    def mark_training_ready(self) -> None:
        """بررسی و علامت‌گذاری آمادگی برای آموزش"""
        self.is_training_ready = all([
            self.has_readme,
            self.has_sufficient_issues,
            self.has_sufficient_prs,
            self.has_sufficient_code,
        ])


class ValidationResult(BaseModel):
    """نتیجه اعتبارسنجی یک مخزن"""

    full_name: str
    has_readme: bool = False
    issue_count: int = 0
    pr_count: int = 0
    code_file_count: int = 0
    is_valid: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────
# لایه دیتابیس
# ──────────────────────────────────────────────

class RepositoryDB:
    """مدیریت دیتابیس SQLite"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """ساخت جداول"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repositories (
                    full_name          TEXT PRIMARY KEY,
                    owner              TEXT NOT NULL,
                    name               TEXT NOT NULL,
                    description        TEXT,
                    html_url           TEXT,
                    clone_url          TEXT,
                    language           TEXT,
                    stars              INTEGER DEFAULT 0,
                    forks              INTEGER DEFAULT 0,
                    open_issues        INTEGER DEFAULT 0,
                    default_branch     TEXT DEFAULT 'main',
                    created_at         TEXT,
                    updated_at         TEXT,
                    topics             TEXT,
                    has_readme         INTEGER DEFAULT 0,
                    has_sufficient_issues  INTEGER DEFAULT 0,
                    has_sufficient_prs     INTEGER DEFAULT 0,
                    has_sufficient_code    INTEGER DEFAULT 0,
                    is_training_ready  INTEGER DEFAULT 0,
                    migrated           INTEGER DEFAULT 0,
                    discovered_at      TEXT,
                    last_synced        TEXT,
                    rejection_reason   TEXT,
                    keyword_source     TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS extracted_data (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_name   TEXT NOT NULL,
                    data_type   TEXT NOT NULL,
                    title       TEXT,
                    content     TEXT,
                    metadata    TEXT,
                    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (repo_name) REFERENCES repositories(full_name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rejected_repos (
                    full_name   TEXT PRIMARY KEY,
                    reason      TEXT,
                    checked_at  TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_extracted_repo
                ON extracted_data(repo_name, data_type)
            """)
        log.debug("دیتابیس آماده شد")

    def upsert_repository(
        self, repo: RepositoryInfo, keyword: str = ""
    ) -> None:
        """درج یا به‌روزرسانی مخزن"""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO repositories
                    (full_name, owner, name, description, html_url, clone_url,
                     language, stars, forks, open_issues, default_branch,
                     created_at, updated_at, topics, has_readme,
                     has_sufficient_issues, has_sufficient_prs,
                     has_sufficient_code, is_training_ready,
                     migrated, discovered_at, last_synced,
                     rejection_reason, keyword_source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(full_name) DO UPDATE SET
                    description=excluded.description,
                    stars=excluded.stars,
                    forks=excluded.forks,
                    open_issues=excluded.open_issues,
                    updated_at=excluded.updated_at,
                    topics=excluded.topics,
                    has_readme=excluded.has_readme,
                    has_sufficient_issues=excluded.has_sufficient_issues,
                    has_sufficient_prs=excluded.has_sufficient_prs,
                    has_sufficient_code=excluded.has_sufficient_code,
                    is_training_ready=excluded.is_training_ready,
                    last_synced=excluded.last_synced,
                    rejection_reason=excluded.rejection_reason
                """,
                (
                    repo.full_name, repo.owner, repo.name, repo.description,
                    repo.html_url, repo.clone_url, repo.language, repo.stars,
                    repo.forks, repo.open_issues, repo.default_branch,
                    repo.created_at, repo.updated_at,
                    ",".join(repo.topics),
                    int(repo.has_readme),
                    int(repo.has_sufficient_issues),
                    int(repo.has_sufficient_prs),
                    int(repo.has_sufficient_code),
                    int(repo.is_training_ready),
                    int(repo.migrated),
                    repo.discovered_at,
                    datetime.utcnow().isoformat(),
                    repo.rejection_reason,
                    keyword,
                ),
            )

    def save_extracted_data(
        self,
        repo_name: str,
        data_type: str,
        title: str,
        content: str,
        metadata: str = "",
    ) -> None:
        """ذخیره داده استخراج‌شده"""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO extracted_data
                   (repo_name, data_type, title, content, metadata)
                   VALUES (?,?,?,?,?)""",
                (repo_name, data_type, title, content, metadata),
            )

    def save_rejected(self, full_name: str, reason: str) -> None:
        """ذخیره مخزن رد شده"""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO rejected_repos
                   (full_name, reason, checked_at)
                   VALUES (?,?,?)""",
                (full_name, reason, datetime.utcnow().isoformat()),
            )

    def is_already_checked(self, full_name: str) -> bool:
        """آیا قبلاً بررسی شده؟"""
        with self._get_conn() as conn:
            r1 = conn.execute(
                "SELECT 1 FROM repositories WHERE full_name=?", (full_name,)
            ).fetchone()
            r2 = conn.execute(
                "SELECT 1 FROM rejected_repos WHERE full_name=?", (full_name,)
            ).fetchone()
            return r1 is not None or r2 is not None

    def mark_migrated(self, full_name: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE repositories SET migrated=1 WHERE full_name=?",
                (full_name,),
            )

    def is_migrated(self, full_name: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT migrated FROM repositories WHERE full_name=?",
                (full_name,),
            ).fetchone()
            return bool(row and row["migrated"])

    def get_unmigrated_training_ready(self) -> list[dict]:
        """مخازن آماده آموزش که هنوز منتقل نشده‌اند"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM repositories
                   WHERE migrated=0 AND is_training_ready=1
                   ORDER BY stars DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_training_ready(self) -> list[dict]:
        """همه مخازن آماده آموزش"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM repositories
                   WHERE is_training_ready=1
                   ORDER BY stars DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM repositories ORDER BY stars DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """آمار کامل"""
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM repositories"
            ).fetchone()["c"]
            training_ready = conn.execute(
                "SELECT COUNT(*) as c FROM repositories WHERE is_training_ready=1"
            ).fetchone()["c"]
            migrated = conn.execute(
                "SELECT COUNT(*) as c FROM repositories WHERE migrated=1"
            ).fetchone()["c"]
            rejected = conn.execute(
                "SELECT COUNT(*) as c FROM rejected_repos"
            ).fetchone()["c"]
            data_count = conn.execute(
                "SELECT COUNT(*) as c FROM extracted_data"
            ).fetchone()["c"]

            # آمار به تفکیک نوع داده
            type_stats = {}
            for row in conn.execute(
                """SELECT data_type, COUNT(*) as c
                   FROM extracted_data GROUP BY data_type"""
            ).fetchall():
                type_stats[row["data_type"]] = row["c"]

        return {
            "total_repos": total,
            "training_ready": training_ready,
            "migrated": migrated,
            "pending_migration": training_ready - migrated,
            "rejected": rejected,
            "total_extracted_records": data_count,
            "extracted_by_type": type_stats,
        }