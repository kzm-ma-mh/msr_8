"""
استخراج‌کننده کامل داده‌ها
README, Issues, Pull Requests, Code
"""

from __future__ import annotations

import json
import time

from config.settings import (
    MAX_ISSUES_EXTRACT,
    MAX_PRS_EXTRACT,
    MAX_CODE_FILES_EXTRACT,
    CODE_EXTENSIONS,
)
from core.rate_limiter import GitHubRateLimiter
from models.repository import RepositoryDB, RepositoryInfo
from utils.helpers import decode_base64_content, truncate
from utils.logger import log


class DataExtractor:
    """استخراج کامل داده‌های آموزشی از مخازن واجد شرایط"""

    def __init__(
        self,
        db: RepositoryDB | None = None,
        rate_limiter: GitHubRateLimiter | None = None,
    ):
        self.api = rate_limiter or GitHubRateLimiter()
        self.db = db or RepositoryDB()

    # ──────────────────────────────────────────
    # README
    # ──────────────────────────────────────────

    def extract_readme(self, repo: RepositoryInfo) -> str | None:
        """استخراج و ذخیره README"""
        log.debug(f"   📄 README: {repo.full_name}")

        resp = self.api.get(f"/repos/{repo.full_name}/readme")
        if resp.status_code != 200:
            return None

        data = resp.json()
        content = decode_base64_content(data.get("content", ""))

        if content:
            self.db.save_extracted_data(
                repo_name=repo.full_name,
                data_type="readme",
                title=data.get("name", "README.md"),
                content=content,
                metadata=json.dumps({
                    "size": data.get("size", 0),
                    "path": data.get("path", ""),
                    "sha": data.get("sha", ""),
                    "encoding": data.get("encoding", ""),
                }),
            )
            log.debug(f"   ✅ README: {len(content)} chars")

        time.sleep(0.3)
        return content

    # ──────────────────────────────────────────
    # Issues
    # ──────────────────────────────────────────

    def extract_issues(
        self, repo: RepositoryInfo, max_count: int | None = None
    ) -> list[dict]:
        """استخراج Issues با جزئیات کامل (شامل کامنت‌ها)"""
        max_count = max_count or MAX_ISSUES_EXTRACT
        log.debug(f"   🐛 Issues (max {max_count}): {repo.full_name}")

        issues: list[dict] = []
        page = 1

        while len(issues) < max_count:
            resp = self.api.get(
                f"/repos/{repo.full_name}/issues",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
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
                if len(issues) >= max_count:
                    break
                # فیلتر PRها
                if "pull_request" in item:
                    continue

                # دریافت کامنت‌های Issue
                comments_text = ""
                if item.get("comments", 0) > 0:
                    comments_text = self._fetch_issue_comments(
                        repo.full_name, item["number"]
                    )

                issue_data = {
                    "number": item.get("number"),
                    "title": item.get("title", ""),
                    "state": item.get("state", ""),
                    "body": item.get("body", "") or "",
                    "labels": [lb.get("name", "") for lb in item.get("labels", [])],
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "comments_count": item.get("comments", 0),
                    "comments_text": comments_text,
                    "user": item.get("user", {}).get("login", ""),
                }
                issues.append(issue_data)

                self.db.save_extracted_data(
                    repo_name=repo.full_name,
                    data_type="issue",
                    title=f"#{issue_data['number']}: {issue_data['title']}",
                    content=json.dumps({
                        "body": issue_data["body"],
                        "comments": comments_text,
                    }, ensure_ascii=False),
                    metadata=json.dumps({
                        "state": issue_data["state"],
                        "labels": issue_data["labels"],
                        "comments_count": issue_data["comments_count"],
                        "user": issue_data["user"],
                        "created_at": issue_data["created_at"],
                    }),
                )

            page += 1
            time.sleep(0.5)

        log.debug(f"   ✅ {len(issues)} Issues ذخیره شد")
        return issues

    def _fetch_issue_comments(
        self, full_name: str, issue_number: int, max_comments: int = 10
    ) -> str:
        """دریافت کامنت‌های یک Issue"""
        resp = self.api.get(
            f"/repos/{full_name}/issues/{issue_number}/comments",
            params={"per_page": max_comments},
        )
        if resp.status_code != 200:
            return ""

        comments = resp.json()
        parts = []
        for c in comments:
            user = c.get("user", {}).get("login", "unknown")
            body = c.get("body", "")
            parts.append(f"[{user}]: {body}")

        time.sleep(0.3)
        return "\n---\n".join(parts)

    # ──────────────────────────────────────────
    # Pull Requests
    # ──────────────────────────────────────────

    def extract_pull_requests(
        self, repo: RepositoryInfo, max_count: int | None = None
    ) -> list[dict]:
        """استخراج Pull Requests با diff summary"""
        max_count = max_count or MAX_PRS_EXTRACT
        log.debug(f"   🔀 PRs (max {max_count}): {repo.full_name}")

        prs: list[dict] = []
        page = 1

        while len(prs) < max_count:
            resp = self.api.get(
                f"/repos/{repo.full_name}/pulls",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
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
                if len(prs) >= max_count:
                    break

                # دریافت اطلاعات فایل‌های تغییر یافته
                changed_files = self._fetch_pr_files(
                    repo.full_name, item["number"]
                )

                pr_data = {
                    "number": item.get("number"),
                    "title": item.get("title", ""),
                    "state": item.get("state", ""),
                    "body": item.get("body", "") or "",
                    "merged": item.get("merged_at") is not None,
                    "head_branch": item.get("head", {}).get("ref", ""),
                    "base_branch": item.get("base", {}).get("ref", ""),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "user": item.get("user", {}).get("login", ""),
                    "changed_files": changed_files,
                    "additions": item.get("additions", 0),
                    "deletions": item.get("deletions", 0),
                }
                prs.append(pr_data)

                self.db.save_extracted_data(
                    repo_name=repo.full_name,
                    data_type="pull_request",
                    title=f"PR #{pr_data['number']}: {pr_data['title']}",
                    content=json.dumps({
                        "body": pr_data["body"],
                        "changed_files": changed_files,
                    }, ensure_ascii=False),
                    metadata=json.dumps({
                        "state": pr_data["state"],
                        "merged": pr_data["merged"],
                        "head": pr_data["head_branch"],
                        "base": pr_data["base_branch"],
                        "user": pr_data["user"],
                        "additions": pr_data["additions"],
                        "deletions": pr_data["deletions"],
                    }),
                )

            page += 1
            time.sleep(0.5)

        log.debug(f"   ✅ {len(prs)} PRs ذخیره شد")
        return prs

    def _fetch_pr_files(
        self, full_name: str, pr_number: int, max_files: int = 20
    ) -> list[dict]:
        """دریافت لیست فایل‌های تغییریافته در PR"""
        resp = self.api.get(
            f"/repos/{full_name}/pulls/{pr_number}/files",
            params={"per_page": max_files},
        )
        if resp.status_code != 200:
            return []

        files = []
        for f in resp.json():
            files.append({
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": truncate(f.get("patch", ""), 3000),
            })

        time.sleep(0.3)
        return files

    # ──────────────────────────────────────────
    # Code Files
    # ──────────────────────────────────────────

    def extract_code_files(
        self,
        repo: RepositoryInfo,
        max_files: int | None = None,
        max_file_size: int = 80_000,
    ) -> list[dict]:
        """استخراج فایل‌های کد مهم"""
        max_files = max_files or MAX_CODE_FILES_EXTRACT
        log.debug(f"   💻 Code (max {max_files}): {repo.full_name}")

        resp = self.api.get(
            f"/repos/{repo.full_name}/git/trees/{repo.default_branch}",
            params={"recursive": "1"},
        )
        if resp.status_code != 200:
            return []

        tree = resp.json().get("tree", [])

        # فیلتر و اولویت‌بندی فایل‌ها
        candidates = [
            node for node in tree
            if (
                node.get("type") == "blob"
                and any(
                    node.get("path", "").endswith(ext) for ext in CODE_EXTENSIONS
                )
                and node.get("size", 0) <= max_file_size
                and not self._is_generated_file(node.get("path", ""))
            )
        ]

        # اولویت: فایل‌های اصلی‌تر (عمق کمتر، سایز متوسط)
        candidates.sort(key=lambda n: (
            n.get("path", "").count("/"),  # عمق کمتر اول
            abs(n.get("size", 0) - 5000),  # نزدیک به 5KB ترجیح
        ))
        candidates = candidates[:max_files]

        code_files: list[dict] = []

        for node in candidates:
            file_resp = self.api.get(
                f"/repos/{repo.full_name}/contents/{node['path']}",
                params={"ref": repo.default_branch},
            )
            if file_resp.status_code != 200:
                continue

            file_data = file_resp.json()
            content = decode_base64_content(file_data.get("content", ""))

            if not content or len(content.strip()) < 50:
                continue

            file_info = {
                "path": node["path"],
                "size": node.get("size", 0),
                "content": content,
            }
            code_files.append(file_info)

            self.db.save_extracted_data(
                repo_name=repo.full_name,
                data_type="code",
                title=node["path"],
                content=content,
                metadata=json.dumps({
                    "size": node.get("size", 0),
                    "sha": node.get("sha", ""),
                    "language": self._detect_language(node["path"]),
                }),
            )
            time.sleep(0.3)

        log.debug(f"   ✅ {len(code_files)} code files ذخیره شد")
        return code_files

    @staticmethod
    def _is_generated_file(path: str) -> bool:
        """تشخیص فایل‌های تولید خودکار (نامناسب برای آموزش)"""
        skip_patterns = (
            "node_modules/", "vendor/", ".min.", "dist/",
            "build/", "__pycache__/", ".egg-info/",
            "migrations/", "package-lock.json", "yarn.lock",
            "Pipfile.lock", "poetry.lock", ".generated.",
            "test_", "tests/", "_test.", ".test.",
            "setup.py", "setup.cfg", "conftest.py",
        )
        path_lower = path.lower()
        return any(pattern in path_lower for pattern in skip_patterns)

    @staticmethod
    def _detect_language(path: str) -> str:
        """تشخیص زبان از پسوند"""
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".go": "go", ".rs": "rust", ".java": "java",
            ".cpp": "cpp", ".c": "c", ".rb": "ruby",
        }
        for ext, lang in ext_map.items():
            if path.endswith(ext):
                return lang
        return "unknown"

    # ──────────────────────────────────────────
    # استخراج کامل
    # ──────────────────────────────────────────

    def extract_all(self, repo: RepositoryInfo) -> dict:
        """استخراج تمام داده‌های آموزشی یک مخزن"""
        log.info(
            f"📥 استخراج کامل: [bold]{repo.full_name}[/] "
            f"⭐{repo.stars} | 🔤{repo.language or 'N/A'}"
        )

        result = {
            "readme": self.extract_readme(repo),
            "issues": self.extract_issues(repo),
            "pull_requests": self.extract_pull_requests(repo),
            "code_files": self.extract_code_files(repo),
        }

        # خلاصه
        summary = {
            k: len(v) if isinstance(v, list) else (1 if v else 0)
            for k, v in result.items()
        }
        log.info(f"   📊 نتیجه: {summary}")

        return result