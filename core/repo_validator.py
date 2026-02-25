"""
اعتبارسنج مخازن برای داده آموزشی
بررسی سریع وجود README, Issues, PRs, Code
قبل از استخراج کامل — صرفه‌جویی در API calls
"""

from __future__ import annotations

import time

from config.settings import (
    MIN_ISSUES_REQUIRED,
    MIN_PRS_REQUIRED,
    MIN_CODE_FILES_REQUIRED,
    CODE_EXTENSIONS,
)
from core.rate_limiter import GitHubRateLimiter
from models.repository import RepositoryInfo, ValidationResult
from utils.logger import log


class RepoValidator:
    """
    اعتبارسنجی سریع مخزن
    فقط HEAD request یا حداقل API call برای بررسی وجود داده
    """

    def __init__(self, rate_limiter: GitHubRateLimiter | None = None):
        self.api = rate_limiter or GitHubRateLimiter()

    def validate(self, repo: RepositoryInfo) -> ValidationResult:
        """
        بررسی اینکه مخزن تمام شرایط داده آموزشی را دارد:
        ✅ README موجود
        ✅ حداقل N عدد Issue (غیر PR)
        ✅ حداقل N عدد Pull Request
        ✅ حداقل N فایل کد با پسوند مجاز
        """
        result = ValidationResult(full_name=repo.full_name)

        log.debug(f"   🔎 اعتبارسنجی: {repo.full_name}")

        # ── ۱. بررسی README ──
        result.has_readme = self._check_readme(repo)
        if not result.has_readme:
            result.rejection_reasons.append("❌ README ندارد")
            result.is_valid = False
            return result

        # ── ۲. بررسی Issues ──
        result.issue_count = self._count_issues(repo)
        if result.issue_count < MIN_ISSUES_REQUIRED:
            result.rejection_reasons.append(
                f"❌ Issues ناکافی: {result.issue_count}/{MIN_ISSUES_REQUIRED}"
            )

        # ── ۳. بررسی Pull Requests ──
        result.pr_count = self._count_pull_requests(repo)
        if result.pr_count < MIN_PRS_REQUIRED:
            result.rejection_reasons.append(
                f"❌ PRs ناکافی: {result.pr_count}/{MIN_PRS_REQUIRED}"
            )

        # ── ۴. بررسی فایل‌های کد ──
        result.code_file_count = self._count_code_files(repo)
        if result.code_file_count < MIN_CODE_FILES_REQUIRED:
            result.rejection_reasons.append(
                f"❌ Code files ناکافی: {result.code_file_count}/{MIN_CODE_FILES_REQUIRED}"
            )

        # ── نتیجه نهایی ──
        result.is_valid = len(result.rejection_reasons) == 0

        if result.is_valid:
            log.debug(
                f"   ✅ واجد شرایط: README=✓ | "
                f"Issues={result.issue_count} | "
                f"PRs={result.pr_count} | "
                f"Code={result.code_file_count}"
            )
        else:
            reasons = " | ".join(result.rejection_reasons)
            log.debug(f"   ⛔ رد شد: {reasons}")

        return result

    def _check_readme(self, repo: RepositoryInfo) -> bool:
        """بررسی وجود README (یک API call)"""
        resp = self.api.get(f"/repos/{repo.full_name}/readme")
        time.sleep(0.3)
        return resp.status_code == 200

    def _count_issues(self, repo: RepositoryInfo) -> int:
        """
        شمارش Issues واقعی (بدون PRها)
        از open_issues_count API نمی‌شود استفاده کرد چون PR ها هم شامل می‌شود
        """
        count = 0
        page = 1

        while True:
            resp = self.api.get(
                f"/repos/{repo.full_name}/issues",
                params={
                    "state": "all",
                    "per_page": 30,
                    "page": page,
                },
            )
            if resp.status_code != 200:
                break

            items = resp.json()
            if not items:
                break

            for item in items:
                if "pull_request" not in item:
                    count += 1

            # اگر به حداقل رسیدیم، نیازی به ادامه نیست
            if count >= MIN_ISSUES_REQUIRED:
                break

            # اگر کمتر از per_page آمد، صفحه آخر بود
            if len(items) < 30:
                break

            page += 1
            time.sleep(0.3)

        return count

    def _count_pull_requests(self, repo: RepositoryInfo) -> int:
        """شمارش Pull Requests"""
        resp = self.api.get(
            f"/repos/{repo.full_name}/pulls",
            params={"state": "all", "per_page": MIN_PRS_REQUIRED + 5},
        )
        time.sleep(0.3)

        if resp.status_code != 200:
            return 0

        return len(resp.json())

    def _count_code_files(self, repo: RepositoryInfo) -> int:
        """شمارش فایل‌های کد با پسوند مجاز"""
        resp = self.api.get(
            f"/repos/{repo.full_name}/git/trees/{repo.default_branch}",
            params={"recursive": "1"},
        )
        time.sleep(0.3)

        if resp.status_code != 200:
            return 0

        tree = resp.json().get("tree", [])
        count = sum(
            1
            for node in tree
            if node.get("type") == "blob"
            and any(node.get("path", "").endswith(ext) for ext in CODE_EXTENSIONS)
            and node.get("size", 0) <= 100_000  # فایل‌های زیر 100KB
        )
        return count