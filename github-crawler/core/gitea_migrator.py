"""
انتقال مخازن به Gitea — با پشتیبانی Organization
"""

from __future__ import annotations

import time
import requests

from config.settings import (
    GITEA_API_BASE,
    GITEA_HEADERS,
    GITEA_URL,
    GITEA_ORG,
    GITHUB_TOKEN,
)
from models.repository import RepositoryDB, RepositoryInfo
from utils.logger import log


class GiteaMigrator:
    """انتقال مخازن از GitHub به Gitea Organization"""

    def __init__(self, db: RepositoryDB | None = None):
        self.db = db or RepositoryDB()
        self._session = requests.Session()
        self._session.headers.update(GITEA_HEADERS)
        self._current_user: str | None = None
        self._org: str = GITEA_ORG

    # ──────────────────────────────────────────
    # اتصال
    # ──────────────────────────────────────────

    def verify_connection(self) -> bool:
        """بررسی اتصال و وجود Organization"""
        try:
            # بررسی کاربر
            resp = self._session.get(f"{GITEA_API_BASE}/user", timeout=10)
            if resp.status_code != 200:
                log.error(f"❌ خطای احراز هویت Gitea: {resp.status_code}")
                return False

            self._current_user = resp.json().get("login", "unknown")
            log.info(f"✅ Gitea کاربر: [bold green]{self._current_user}[/]")

            # بررسی Organization
            org_resp = self._session.get(
                f"{GITEA_API_BASE}/orgs/{self._org}", timeout=10
            )
            if org_resp.status_code == 200:
                log.info(f"✅ Organization موجود: [bold green]{self._org}[/]")
            elif org_resp.status_code == 404:
                log.warning(f"⚠️ Organization '{self._org}' وجود ندارد — ساخته می‌شود")
                if not self._create_org():
                    return False
            else:
                log.error(f"❌ خطای بررسی org: {org_resp.status_code}")
                return False

            return True

        except requests.ConnectionError:
            log.error(f"❌ عدم دسترسی به Gitea: {GITEA_URL}")
            return False

    def _create_org(self) -> bool:
        """ساخت خودکار Organization"""
        resp = self._session.post(
            f"{GITEA_API_BASE}/orgs",
            json={
                "username": self._org,
                "full_name": "GitHub Mirror Projects",
                "description": "Mirrored repos from GitHub for training data",
                "visibility": "public",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            log.info(f"✅ Organization '{self._org}' ساخته شد")
            return True
        log.error(f"❌ خطا در ساخت org: {resp.status_code} — {resp.text[:200]}")
        return False

    @property
    def current_user(self) -> str:
        if not self._current_user:
            self.verify_connection()
        return self._current_user or "unknown"

    # ──────────────────────────────────────────
    # بررسی وجود
    # ──────────────────────────────────────────

    def repo_exists_in_gitea(self, repo_name: str) -> bool:
        """بررسی وجود مخزن در Organization"""
        resp = self._session.get(
            f"{GITEA_API_BASE}/repos/{self._org}/{repo_name}",
            timeout=10,
        )
        return resp.status_code == 200

    # ──────────────────────────────────────────
    # انتقال
    # ──────────────────────────────────────────

    def migrate_repository(
        self,
        repo: RepositoryInfo,
        include_all: bool = True,
    ) -> bool:
        """
        انتقال کامل مخزن به Organization در Gitea
        شامل: Git history, Issues, PRs, Labels, Releases, Wiki
        """
        log.info(
            f"🚀 انتقال: [bold]{repo.full_name}[/] → "
            f"[bold cyan]{self._org}/{repo.name}[/]"
        )

        if self.repo_exists_in_gitea(repo.name):
            log.warning(f"   ⚠️ {self._org}/{repo.name} از قبل وجود دارد")
            self.db.mark_migrated(repo.full_name)
            return True

        payload = {
            "clone_addr": repo.clone_url,
            "auth_token": GITHUB_TOKEN,
            "mirror": False,
            "private": False,
            "repo_name": repo.name,
            "repo_owner": self._org,       # ← به Organization منتقل می‌شود
            "service": "github",
            "description": (repo.description or "")[:255],
            # حفظ تمام داده‌ها
            "issues": include_all,
            "labels": include_all,
            "milestones": include_all,
            "pull_requests": include_all,
            "releases": include_all,
            "wiki": include_all,
            "lfs": False,
        }

        try:
            resp = self._session.post(
                f"{GITEA_API_BASE}/repos/migrate",
                json=payload,
                timeout=600,  # ۱۰ دقیقه برای مخازن بزرگ
            )

            if resp.status_code in (200, 201):
                gitea_data = resp.json()
                gitea_url = gitea_data.get(
                    "html_url",
                    f"{GITEA_URL}/{self._org}/{repo.name}",
                )
                log.info(f"   ✅ موفق: [link={gitea_url}]{gitea_url}[/link]")
                self.db.mark_migrated(repo.full_name)
                return True

            elif resp.status_code == 409:
                log.warning(f"   ⚠️ تکراری (409)")
                self.db.mark_migrated(repo.full_name)
                return True

            else:
                log.error(
                    f"   ❌ خطا {resp.status_code}: {resp.text[:500]}"
                )
                return False

        except requests.Timeout:
            log.error(f"   ❌ Timeout (مخزن خیلی بزرگ است)")
            return False
        except requests.RequestException as e:
            log.error(f"   ❌ خطای شبکه: {e}")
            return False

    def migrate_all_pending(self) -> dict[str, int]:
        """انتقال تمام مخازن آماده"""
        if not self.verify_connection():
            return {"success": 0, "failed": 0, "total": 0}

        pending = self.db.get_unmigrated_training_ready()
        total = len(pending)
        success = failed = 0

        if total == 0:
            log.info("✅ همه منتقل شده‌اند")
            return {"success": 0, "failed": 0, "total": 0}

        log.info(f"📋 {total} مخزن در صف → {self._org}")

        for i, row in enumerate(pending, 1):
            log.info(f"── [{i}/{total}] ──")
            repo = RepositoryInfo(
                full_name=row["full_name"],
                owner=row["owner"],
                name=row["name"],
                description=row.get("description"),
                html_url=row.get("html_url", ""),
                clone_url=row.get("clone_url", ""),
                language=row.get("language"),
                stars=row.get("stars", 0),
                forks=row.get("forks", 0),
                default_branch=row.get("default_branch", "main"),
                is_training_ready=True,
            )
            if self.migrate_repository(repo):
                success += 1
            else:
                failed += 1
            if i < total:
                time.sleep(5)

        result = {"success": success, "failed": failed, "total": total}
        log.info(f"📊 نتیجه: {result}")
        return result