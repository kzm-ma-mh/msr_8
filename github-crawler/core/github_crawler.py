"""
کرولر هوشمند GitHub
- جستجوی صفحه‌بندی‌شده
- اعتبارسنجی اجباری داده آموزشی
- تعداد پروژه هر کلیدواژه قابل تنظیم
"""

from __future__ import annotations

import time

from config.settings import (
    SEARCH_KEYWORDS,
    SEARCH_LANGUAGE,
    MIN_STARS,
    PROJECTS_PER_KEYWORD,
    MAX_SCAN_PER_KEYWORD,
)
from core.rate_limiter import GitHubRateLimiter
from core.repo_validator import RepoValidator
from models.repository import RepositoryInfo, RepositoryDB, ValidationResult
from utils.logger import log


class GitHubCrawler:
    """کرولر مخازن با فیلتر داده آموزشی"""

    def __init__(self, db: RepositoryDB | None = None):
        self.rate_limiter = GitHubRateLimiter()
        self.validator = RepoValidator(self.rate_limiter)
        self.db = db or RepositoryDB()
        self._seen: set[str] = set()

    def search_repositories(
        self,
        keywords: list[str] | None = None,
        language: str | None = None,
        min_stars: int | None = None,
        projects_per_keyword: int | None = None,
        max_scan: int | None = None,
    ) -> list[RepositoryInfo]:
        """
        جستجوی مخازن با اعتبارسنجی اجباری
        فقط مخازنی برگردانده می‌شوند که تمام شرایط آموزشی را دارند
        """
        keywords = keywords or SEARCH_KEYWORDS
        language = language or SEARCH_LANGUAGE
        min_stars = min_stars if min_stars is not None else MIN_STARS
        target_count = projects_per_keyword or PROJECTS_PER_KEYWORD
        scan_limit = max_scan or MAX_SCAN_PER_KEYWORD

        all_valid_repos: list[RepositoryInfo] = []

        for keyword in keywords:
            log.info(f"\n{'='*50}")
            log.info(
                f"🔍 کلیدواژه: [bold cyan]{keyword}[/] | "
                f"هدف: {target_count} پروژه واجد شرایط"
            )
            log.info(f"{'='*50}")

            valid_repos = self._search_keyword_with_validation(
                keyword=keyword,
                language=language,
                min_stars=min_stars,
                target_count=target_count,
                scan_limit=scan_limit,
            )
            all_valid_repos.extend(valid_repos)

            log.info(
                f"✅ «{keyword}»: {len(valid_repos)}/{target_count} "
                f"پروژه واجد شرایط یافت شد"
            )
            time.sleep(3)

        log.info(f"\n📦 مجموع پروژه‌های واجد شرایط: "
                 f"[bold green]{len(all_valid_repos)}[/]")
        return all_valid_repos

    def _search_keyword_with_validation(
        self,
        keyword: str,
        language: str,
        min_stars: int,
        target_count: int,
        scan_limit: int,
    ) -> list[RepositoryInfo]:
        """
        جستجو + اعتبارسنجی برای یک کلیدواژه
        تا رسیدن به تعداد هدف یا اتمام نتایج ادامه می‌دهد
        """
        query = f"{keyword} language:{language} stars:>={min_stars}"
        valid_repos: list[RepositoryInfo] = []
        scanned = 0
        rejected = 0
        skipped = 0
        page = 1
        per_page = 30

        while len(valid_repos) < target_count and scanned < scan_limit:
            # ── دریافت صفحه نتایج ──
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            }

            response = self.rate_limiter.get(
                "/search/repositories", params=params, is_search=True
            )

            if response.status_code != 200:
                log.error(f"❌ خطای جستجو: {response.status_code}")
                break

            data = response.json()
            items = data.get("items", [])
            total_available = data.get("total_count", 0)

            if not items:
                log.info("   📭 نتایج تمام شد")
                break

            log.info(
                f"   📄 صفحه {page} — {len(items)} مخزن "
                f"(مجموع در GitHub: {total_available})"
            )

            # ── بررسی هر مخزن ──
            for item in items:
                if len(valid_repos) >= target_count:
                    break
                if scanned >= scan_limit:
                    break

                full_name = item.get("full_name", "")

                # رد تکراری
                if full_name in self._seen:
                    skipped += 1
                    continue
                self._seen.add(full_name)

                # رد بررسی‌شده قبلی
                if self.db.is_already_checked(full_name):
                    skipped += 1
                    continue

                scanned += 1
                repo = RepositoryInfo.from_github_api(item)

                # ── پیش‌فیلتر سریع ──
                # اگر open_issues_count صفر باشد، احتمالاً Issue و PR ندارد
                if repo.open_issues == 0:
                    self._reject_repo(
                        repo, keyword,
                        "open_issues_count=0 (احتمالاً بدون Issue/PR)"
                    )
                    rejected += 1
                    continue

                # ── اعتبارسنجی کامل ──
                log.info(
                    f"   [{scanned}/{scan_limit}] 🔎 بررسی: "
                    f"{full_name} (⭐{repo.stars})"
                )

                validation = self.validator.validate(repo)

                if validation.is_valid:
                    # ✅ واجد شرایط
                    repo.has_readme = True
                    repo.has_sufficient_issues = True
                    repo.has_sufficient_prs = True
                    repo.has_sufficient_code = True
                    repo.mark_training_ready()

                    valid_repos.append(repo)
                    self.db.upsert_repository(repo, keyword=keyword)

                    log.info(
                        f"   ✅ [bold green]قبول[/] [{len(valid_repos)}/{target_count}]: "
                        f"{full_name} | Issues={validation.issue_count} "
                        f"PRs={validation.pr_count} Code={validation.code_file_count}"
                    )
                else:
                    # ❌ رد شد
                    reason = " | ".join(validation.rejection_reasons)
                    self._reject_repo(repo, keyword, reason)
                    rejected += 1
                    log.info(f"   ⛔ رد: {full_name} — {reason}")

                time.sleep(0.5)

            page += 1

            # محدودیت GitHub Search (1000 نتیجه)
            if page > 34:
                log.warning("   ⚠️ به محدودیت 1000 نتیجه GitHub رسیدیم")
                break

            time.sleep(2)

        log.info(
            f"   📊 خلاصه «{keyword}»: "
            f"اسکن={scanned} | قبول={len(valid_repos)} | "
            f"رد={rejected} | رد تکراری={skipped}"
        )

        return valid_repos

    def _reject_repo(
        self, repo: RepositoryInfo, keyword: str, reason: str
    ) -> None:
        """ثبت مخزن رد شده"""
        repo.rejection_reason = reason
        repo.is_training_ready = False
        self.db.save_rejected(repo.full_name, reason)

    def get_repository_details(self, full_name: str) -> RepositoryInfo | None:
        """دریافت جزئیات یک مخزن خاص"""
        response = self.rate_limiter.get(f"/repos/{full_name}")
        if response.status_code != 200:
            log.error(f"❌ خطا در دریافت {full_name}: {response.status_code}")
            return None
        return RepositoryInfo.from_github_api(response.json())