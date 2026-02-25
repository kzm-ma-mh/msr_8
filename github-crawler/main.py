#!/usr/bin/env python3
"""
GitHub Smart Crawler — نسخه نهایی

Usage:
    python main.py --full-crawl ShishirPatil/gorilla        کرول کامل یک مخزن
    python main.py --full-crawl ShishirPatil/gorilla --max-issues 100 --max-prs 50
    python main.py --crawl --per-keyword 10                 کرول خودکار
    python main.py --schedule                               زمان‌بندی
    python main.py --validate owner/repo                    اعتبارسنجی
    python main.py --stats                                  آمار
    python main.py --rate-limit                              وضعیت API
"""

import argparse
import json
import time
import sys

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config.settings import (
    SEARCH_KEYWORDS, SEARCH_LANGUAGE, MIN_STARS,
    PROJECTS_PER_KEYWORD, MAX_SCAN_PER_KEYWORD,
    MIN_ISSUES_REQUIRED, MIN_PRS_REQUIRED, MIN_CODE_FILES_REQUIRED,
    CRON_INTERVAL_HOURS, GITEA_URL, GITEA_ORG,
    GITEA_API_BASE, GITEA_HEADERS, GITHUB_TOKEN,
)
from core.data_extractor import DataExtractor
from core.github_crawler import GitHubCrawler
from core.repo_validator import RepoValidator
from core.rate_limiter import GitHubRateLimiter
from models.repository import RepositoryDB, RepositoryInfo
from scheduler.cron_manager import CronManager
from utils.logger import log

console = Console()


# ══════════════════════════════════════════════════════════
# انتقال‌دهنده یکپارچه (کد + Issues + PRs)
# ══════════════════════════════════════════════════════════

class FullMigrator:
    """
    انتقال کامل مخزن از GitHub به Gitea
    شامل: کد + تاریخچه + Labels + Issues + PRs + Reviews + Comments
    همه مستقیم GitHub API → Gitea API (بدون ذخیره لوکال)
    """

    def __init__(self):
        self.github = GitHubRateLimiter()
        self.gitea = requests.Session()
        self.gitea.headers.update(GITEA_HEADERS)
        self.org = GITEA_ORG
        self.db = RepositoryDB()

    # ──────────────────────────────────────
    # بررسی اتصال
    # ──────────────────────────────────────

    def verify(self) -> bool:
        """بررسی اتصال Gitea و وجود Organization"""
        try:
            r = self.gitea.get(f"{GITEA_API_BASE}/user", timeout=10)
            if r.status_code != 200:
                log.error(f"❌ احراز هویت Gitea: {r.status_code}")
                return False
            user = r.json().get("login", "?")
            log.info(f"✅ Gitea: [bold green]{user}[/]")

            org_r = self.gitea.get(f"{GITEA_API_BASE}/orgs/{self.org}", timeout=10)
            if org_r.status_code == 404:
                self.gitea.post(
                    f"{GITEA_API_BASE}/orgs",
                    json={
                        "username": self.org,
                        "full_name": "GitHub Mirror",
                        "visibility": "public",
                    },
                    timeout=10,
                )
                log.info(f"✅ Organization '{self.org}' ساخته شد")
            else:
                log.info(f"✅ Organization: [bold green]{self.org}[/]")
            return True
        except requests.ConnectionError:
            log.error(f"❌ Gitea در دسترس نیست: {GITEA_URL}")
            return False

    def repo_exists(self, name: str) -> bool:
        r = self.gitea.get(
            f"{GITEA_API_BASE}/repos/{self.org}/{name}", timeout=10
        )
        return r.status_code == 200

    def delete_repo(self, name: str):
        self.gitea.delete(
            f"{GITEA_API_BASE}/repos/{self.org}/{name}", timeout=10
        )
        time.sleep(3)

    # ──────────────────────────────────────
    # مرحله ۱: انتقال کد
    # ──────────────────────────────────────

    def migrate_code(self, github_repo: str, repo_name: str) -> bool:
        """انتقال کد با ۲ استراتژی"""

        if self.repo_exists(repo_name):
            log.info(f"📦 {self.org}/{repo_name} موجوده — کد رد شد")
            return True

        # ── روش ۱: Migration مستقیم ──
        log.info("📦 روش ۱: Migration مستقیم...")
        r = self.gitea.post(
            f"{GITEA_API_BASE}/repos/migrate",
            json={
                "clone_addr": f"https://github.com/{github_repo}.git",
                "auth_token": GITHUB_TOKEN,
                "mirror": False,
                "private": False,
                "repo_name": repo_name,
                "repo_owner": self.org,
                "service": "github",
                "issues": False,
                "labels": False,
                "milestones": False,
                "pull_requests": False,
                "releases": True,
                "wiki": False,
                "lfs": False,
            },
            timeout=3600,
        )

        if r.status_code in (200, 201):
            log.info("✅ کد منتقل شد")
            return True
        if r.status_code == 409:
            log.info("✅ قبلاً وجود داره")
            return True

        log.warning(f"⚠️ روش ۱: {r.status_code}")

        # ── روش ۲: Mirror ──
        log.info("🪞 روش ۲: Mirror...")
        if self.repo_exists(repo_name):
            self.delete_repo(repo_name)

        r = self.gitea.post(
            f"{GITEA_API_BASE}/repos/migrate",
            json={
                "clone_addr": f"https://github.com/{github_repo}.git",
                "auth_token": GITHUB_TOKEN,
                "mirror": True,
                "mirror_interval": "10m",
                "private": False,
                "repo_name": repo_name,
                "repo_owner": self.org,
                "service": "github",
                "issues": False,
                "labels": False,
                "pull_requests": False,
                "releases": False,
                "wiki": False,
                "lfs": False,
            },
            timeout=3600,
        )

        if r.status_code in (200, 201):
            log.info("✅ Mirror ساخته شد — منتظر sync...")
            self._wait_sync(repo_name)
            self.gitea.patch(
                f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}",
                json={"mirror": False},
                timeout=10,
            )
            log.info("✅ Mirror → مخزن عادی")
            return True

        log.error(f"❌ هر دو روش ناموفق: {r.status_code}")
        return False

    def _wait_sync(self, repo_name: str, max_wait=1800, interval=20):
        elapsed = 0
        while elapsed < max_wait:
            r = self.gitea.get(
                f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}", timeout=10
            )
            if r.status_code == 200:
                d = r.json()
                if not d.get("empty", True) and d.get("size", 0) > 100:
                    return True
                log.info(f"   ⏳ [{elapsed}s] size={d.get('size', 0)}KB")
            time.sleep(interval)
            elapsed += interval
        return False

    # ──────────────────────────────────────
    # مرحله ۲: Labels
    # ──────────────────────────────────────

    def migrate_labels(self, github_repo: str, repo_name: str) -> dict[str, int]:
        log.info("🏷️ انتقال Labels...")
        label_map = {}
        page = 1

        while True:
            r = self.github.get(
                f"/repos/{github_repo}/labels",
                params={"per_page": 100, "page": page},
            )
            if r.status_code != 200 or not r.json():
                break

            for lb in r.json():
                color = lb.get("color", "ee0701")
                if not color.startswith("#"):
                    color = f"#{color}"

                gr = self.gitea.post(
                    f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}/labels",
                    json={
                        "name": lb["name"],
                        "color": color,
                        "description": lb.get("description", "") or "",
                    },
                    timeout=10,
                )
                if gr.status_code in (200, 201):
                    label_map[lb["name"]] = gr.json()["id"]
                elif gr.status_code == 409:
                    existing = self.gitea.get(
                        f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}/labels",
                        params={"limit": 100}, timeout=10,
                    )
                    if existing.status_code == 200:
                        for el in existing.json():
                            if el["name"] == lb["name"]:
                                label_map[lb["name"]] = el["id"]
                time.sleep(0.2)
            page += 1

        log.info(f"   ✅ {len(label_map)} label")
        return label_map

    # ──────────────────────────────────────
    # مرحله ۳: Issues
    # ──────────────────────────────────────

    def migrate_issues(
        self, github_repo: str, repo_name: str,
        label_map: dict, max_issues: int = 500
    ) -> int:
        log.info(f"🐛 انتقال Issues (max {max_issues})...")
        count = 0
        page = 1

        while count < max_issues:
            r = self.github.get(
                f"/repos/{github_repo}/issues",
                params={
                    "state": "all", "sort": "created",
                    "direction": "asc", "per_page": 30, "page": page,
                },
            )
            if r.status_code != 200 or not r.json():
                break

            for item in r.json():
                if count >= max_issues:
                    break
                if "pull_request" in item:
                    continue

                user = item.get("user", {}).get("login", "?")
                body = item.get("body", "") or ""
                state = item.get("state", "open")
                created = item.get("created_at", "")

                full_body = (
                    f"📌 *@{user} — {created}*\n"
                    f"🔗 [GitHub]({item.get('html_url', '')})\n\n---\n\n{body}"
                )

                gh_labels = [lb["name"] for lb in item.get("labels", [])]
                ids = [label_map[n] for n in gh_labels if n in label_map]

                gr = self.gitea.post(
                    f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}/issues",
                    json={"title": item["title"], "body": full_body, "labels": ids},
                    timeout=15,
                )

                if gr.status_code in (200, 201):
                    gn = gr.json()["number"]
                    count += 1
                    self._migrate_comments(
                        github_repo, item["number"], repo_name, gn
                    )
                    if state == "closed":
                        self.gitea.patch(
                            f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}"
                            f"/issues/{gn}",
                            json={"state": "closed"}, timeout=10,
                        )
                    if count % 20 == 0:
                        log.info(f"   📊 Issues: {count}")
                time.sleep(0.3)
            page += 1
            time.sleep(1)

        log.info(f"   ✅ {count} Issue")
        return count

    # ──────────────────────────────────────
    # مرحله ۴: Pull Requests
    # ──────────────────────────────────────

    def migrate_prs(
        self, github_repo: str, repo_name: str,
        label_map: dict, max_prs: int = 500
    ) -> int:
        log.info(f"🔀 انتقال Pull Requests (max {max_prs})...")
        count = 0
        page = 1

        while count < max_prs:
            r = self.github.get(
                f"/repos/{github_repo}/pulls",
                params={
                    "state": "all", "sort": "created",
                    "direction": "asc", "per_page": 30, "page": page,
                },
            )
            if r.status_code != 200 or not r.json():
                break

            for pr in r.json():
                if count >= max_prs:
                    break

                success = self._create_pr(github_repo, repo_name, pr, label_map)
                if success:
                    count += 1
                if count % 20 == 0 and count > 0:
                    log.info(f"   📊 PRs: {count}")
                time.sleep(0.5)

            page += 1
            time.sleep(1)

        log.info(f"   ✅ {count} PR")
        return count

    def _create_pr(
        self, github_repo: str, repo_name: str,
        pr: dict, label_map: dict
    ) -> bool:
        """ساخت PR به صورت Issue غنی"""
        number = pr.get("number", 0)
        title = pr.get("title", "")
        user = pr.get("user", {}).get("login", "?")
        body = pr.get("body", "") or ""
        state = pr.get("state", "open")
        merged = pr.get("merged_at") is not None
        created = pr.get("created_at", "")
        head = pr.get("head", {}).get("ref", "?")
        base = pr.get("base", {}).get("ref", "?")
        html_url = pr.get("html_url", "")
        additions = pr.get("additions", 0)
        deletions = pr.get("deletions", 0)
        changed = pr.get("changed_files", 0)

        merge_icon = "✅ Merged" if merged else (
            "❌ Closed" if state == "closed" else "🟡 Open"
        )

        # ── Diff فایل‌ها ──
        files = self._get_pr_files(github_repo, number)
        files_md = ""
        if files:
            files_md = "\n---\n\n### 📁 فایل‌های تغییریافته\n\n"
            for f in files:
                icon = {"added": "🟢", "removed": "🔴", "modified": "🟡",
                        "renamed": "🔵"}.get(f["status"], "⚪")
                files_md += (
                    f"#### {icon} `{f['filename']}` "
                    f"(+{f['additions']} -{f['deletions']})\n\n"
                )
                if f.get("patch"):
                    patch = f["patch"][:3000]
                    if len(f["patch"]) > 3000:
                        patch += "\n... (truncated)"
                    files_md += f"```diff\n{patch}\n```\n\n"

        # ── Reviews ──
        reviews = self._get_pr_reviews(github_repo, number)
        reviews_md = ""
        if reviews:
            reviews_md = "\n---\n\n### 💬 Reviews\n\n"
            for rev in reviews:
                rev_icon = {"APPROVED": "✅", "CHANGES_REQUESTED": "🔴",
                            "COMMENTED": "💬"}.get(rev["state"], "💬")
                reviews_md += (
                    f"{rev_icon} **@{rev['user']}** — {rev['state']}\n\n"
                    f"> {rev['body']}\n\n"
                )

        full_body = (
            f"## 🔀 Pull Request #{number}\n\n"
            f"| فیلد | مقدار |\n|------|-------|\n"
            f"| **نویسنده** | @{user} |\n"
            f"| **تاریخ** | {created} |\n"
            f"| **وضعیت** | {merge_icon} |\n"
            f"| **Branch** | `{head}` → `{base}` |\n"
            f"| **تغییرات** | +{additions} / -{deletions} در {changed} فایل |\n"
            f"| **GitHub** | [Link]({html_url}) |\n\n"
            f"---\n\n### 📝 توضیحات\n\n{body}\n"
            f"{files_md}{reviews_md}"
        )

        gh_labels = [lb["name"] for lb in pr.get("labels", [])]
        ids = [label_map[n] for n in gh_labels if n in label_map]

        resp = self.gitea.post(
            f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}/issues",
            json={
                "title": f"[PR #{number}] {title}",
                "body": full_body,
                "labels": ids,
            },
            timeout=30,
        )

        if resp.status_code not in (200, 201):
            return False

        gn = resp.json()["number"]

        self._migrate_comments(github_repo, number, repo_name, gn)

        if state == "closed" or merged:
            self.gitea.patch(
                f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}/issues/{gn}",
                json={"state": "closed"}, timeout=10,
            )

        return True

    def _get_pr_files(self, github_repo: str, pr_number: int) -> list[dict]:
        r = self.github.get(
            f"/repos/{github_repo}/pulls/{pr_number}/files",
            params={"per_page": 30},
        )
        if r.status_code != 200:
            return []
        time.sleep(0.3)
        return [
            {
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": f.get("patch", ""),
            }
            for f in r.json()
        ]

    def _get_pr_reviews(self, github_repo: str, pr_number: int) -> list[dict]:
        r = self.github.get(
            f"/repos/{github_repo}/pulls/{pr_number}/reviews",
            params={"per_page": 20},
        )
        if r.status_code != 200:
            return []
        time.sleep(0.3)
        return [
            {
                "user": rev.get("user", {}).get("login", "?"),
                "state": rev.get("state", "COMMENTED"),
                "body": rev.get("body", ""),
            }
            for rev in r.json()
            if (rev.get("body") or "").strip()
        ]

    # ──────────────────────────────────────
    # Comments (مشترک)
    # ──────────────────────────────────────

    def _migrate_comments(
        self, github_repo: str, gh_number: int,
        repo_name: str, gitea_number: int
    ):
        r = self.github.get(
            f"/repos/{github_repo}/issues/{gh_number}/comments",
            params={"per_page": 50},
        )
        if r.status_code != 200:
            return
        for c in r.json():
            cu = c.get("user", {}).get("login", "?")
            cb = c.get("body", "")
            ct = c.get("created_at", "")
            self.gitea.post(
                f"{GITEA_API_BASE}/repos/{self.org}/{repo_name}"
                f"/issues/{gitea_number}/comments",
                json={"body": f"💬 *@{cu} — {ct}*\n\n---\n\n{cb}"},
                timeout=10,
            )
            time.sleep(0.2)

    # ──────────────────────────────────────
    # اجرای کامل
    # ──────────────────────────────────────

    def full_migrate(
        self, github_repo: str,
        max_issues: int = 500, max_prs: int = 500,
    ):
        """کرول و انتقال کامل یک مخزن"""
        repo_name = github_repo.split("/")[-1]

        log.info(f"\n{'='*60}")
        log.info(f"🎯 مخزن: [bold cyan]{github_repo}[/]")
        log.info(f"🏢 مقصد: [bold]{self.org}/{repo_name}[/]")
        log.info(f"{'='*60}")

        if not self.verify():
            return

        # ── اطلاعات مخزن ──
        log.info("\n📡 دریافت اطلاعات...")
        r = self.github.get(f"/repos/{github_repo}")
        if r.status_code != 200:
            log.error(f"❌ مخزن یافت نشد: {r.status_code}")
            return
        info = r.json()
        log.info(f"   ⭐ {info.get('stargazers_count', 0)} ستاره")
        log.info(f"   🔤 {info.get('language', 'N/A')}")
        log.info(f"   📝 {info.get('description', 'N/A')}")

        # ── کد ──
        log.info(f"\n{'='*50}")
        log.info("📦 مرحله ۱: انتقال کد + تاریخچه")
        log.info(f"{'='*50}")
        if not self.migrate_code(github_repo, repo_name):
            log.error("❌ انتقال کد ناموفق")
            return

        time.sleep(5)

        # ── Labels ──
        log.info(f"\n{'='*50}")
        log.info("🏷️ مرحله ۲: Labels")
        log.info(f"{'='*50}")
        label_map = self.migrate_labels(github_repo, repo_name)

        # ── Issues ──
        log.info(f"\n{'='*50}")
        log.info("🐛 مرحله ۳: Issues")
        log.info(f"{'='*50}")
        issues_count = self.migrate_issues(
            github_repo, repo_name, label_map, max_issues
        )

        # ── PRs ──
        log.info(f"\n{'='*50}")
        log.info("🔀 مرحله ۴: Pull Requests")
        log.info(f"{'='*50}")
        prs_count = self.migrate_prs(
            github_repo, repo_name, label_map, max_prs
        )

        # ── گزارش ──
        url = f"{GITEA_URL}/{self.org}/{repo_name}"
        log.info(f"\n{'='*60}")
        log.info("🎉 [bold green]کامل شد![/]")
        log.info(f"   🏷️  Labels: {len(label_map)}")
        log.info(f"   🐛 Issues: {issues_count}")
        log.info(f"   🔀 PRs:    {prs_count}")
        log.info(f"   🔗 {url}")
        log.info(f"{'='*60}")

        console.print(Panel(
            f"[bold green]✅ انتقال کامل![/]\n\n"
            f"🔗 {url}\n\n"
            f"🏷️ {len(label_map)} Labels | "
            f"🐛 {issues_count} Issues | "
            f"🔀 {prs_count} PRs",
            title="نتیجه", style="green",
        ))


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════

def print_banner():
    banner = """
╔═══════════════════════════════════════════════════════════╗
║     🔍  GitHub Smart Crawler — Training Data Edition  🎓  ║
║       Crawl → Validate → Extract → Migrate to Gitea       ║
╚═══════════════════════════════════════════════════════════╝"""
    console.print(Panel(banner, style="bold cyan"))

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("📝 کلیدواژه‌ها:", ", ".join(SEARCH_KEYWORDS))
    t.add_row("💻 زبان:", SEARCH_LANGUAGE)
    t.add_row("⭐ حداقل ستاره:", str(MIN_STARS))
    t.add_row("🎯 پروژه/کلیدواژه:", str(PROJECTS_PER_KEYWORD))
    t.add_row("🏠 Gitea:", f"{GITEA_URL} (org: {GITEA_ORG})")
    console.print(t)
    console.print()


def cmd_full_crawl(full_name: str, max_issues: int, max_prs: int):
    migrator = FullMigrator()
    migrator.full_migrate(full_name, max_issues=max_issues, max_prs=max_prs)


def cmd_crawl_only(per_keyword: int | None = None):
    db = RepositoryDB()
    crawler = GitHubCrawler(db)
    extractor = DataExtractor(db, crawler.rate_limiter)

    repos = crawler.search_repositories(projects_per_keyword=per_keyword)
    migrator = FullMigrator()
    if not migrator.verify():
        log.error("❌ Gitea متصل نیست")
        return

    for i, repo in enumerate(repos, 1):
        log.info(f"\n[{i}/{len(repos)}] {repo.full_name}")
        migrator.full_migrate(repo.full_name)

    log.info(f"\n✅ {len(repos)} پروژه کامل شد")


def cmd_schedule():
    manager = CronManager()
    manager.start_scheduler()


def cmd_validate(full_name: str):
    api = GitHubRateLimiter()
    crawler = GitHubCrawler()
    validator = RepoValidator(api)

    repo = crawler.get_repository_details(full_name)
    if not repo:
        log.error(f"❌ {full_name} یافت نشد")
        return

    result = validator.validate(repo)
    t = Table(title=f"اعتبارسنجی {full_name}", show_lines=True)
    t.add_column("معیار", style="cyan")
    t.add_column("وضعیت", justify="center")
    t.add_column("مقدار", justify="center")

    t.add_row("README", "✅" if result.has_readme else "❌",
              "✓" if result.has_readme else "✗")
    t.add_row("Issues",
              "✅" if result.issue_count >= MIN_ISSUES_REQUIRED else "❌",
              str(result.issue_count))
    t.add_row("PRs",
              "✅" if result.pr_count >= MIN_PRS_REQUIRED else "❌",
              str(result.pr_count))
    t.add_row("Code",
              "✅" if result.code_file_count >= MIN_CODE_FILES_REQUIRED else "❌",
              str(result.code_file_count))
    t.add_row("نتیجه",
              "[bold green]✅[/]" if result.is_valid else "[bold red]❌[/]", "")
    console.print(t)


def cmd_stats():
    db = RepositoryDB()
    stats = db.get_stats()
    t = Table(title="📊 آمار", show_lines=True)
    t.add_column("متریک", style="cyan")
    t.add_column("مقدار", style="green", justify="center")
    for k, v in stats.items():
        if isinstance(v, dict):
            for dk, dv in v.items():
                t.add_row(f"  {dk}", str(dv))
        else:
            t.add_row(k, str(v))
    console.print(t)


def cmd_rate_limit():
    api = GitHubRateLimiter()
    api.check_rate_limit()


def main():
    parser = argparse.ArgumentParser(
        description="🔍 GitHub Smart Crawler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--full-crawl", type=str, metavar="OWNER/REPO")
    group.add_argument("--crawl", action="store_true")
    group.add_argument("--schedule", action="store_true")
    group.add_argument("--validate", type=str, metavar="OWNER/REPO")
    group.add_argument("--stats", action="store_true")
    group.add_argument("--rate-limit", action="store_true")

    parser.add_argument("--per-keyword", type=int, default=None)
    parser.add_argument("--max-issues", type=int, default=500)
    parser.add_argument("--max-prs", type=int, default=500)

    args = parser.parse_args()
    print_banner()

    if args.full_crawl:
        cmd_full_crawl(args.full_crawl, args.max_issues, args.max_prs)
    elif args.crawl:
        cmd_crawl_only(args.per_keyword)
    elif args.schedule:
        cmd_schedule()
    elif args.validate:
        cmd_validate(args.validate)
    elif args.stats:
        cmd_stats()
    elif args.rate_limit:
        cmd_rate_limit()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()