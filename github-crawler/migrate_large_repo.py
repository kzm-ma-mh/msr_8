#!/usr/bin/env python3
"""
اسکریپت مخصوص مخازن بزرگ
کد + Issues + Pull Requests + Labels
همه مستقیم GitHub API → Gitea API
"""

import time
import sys
import requests
from config.settings import (
    GITEA_API_BASE, GITEA_HEADERS, GITEA_ORG,
    GITHUB_TOKEN, GITEA_URL,
)
from core.rate_limiter import GitHubRateLimiter
from utils.logger import log

GITHUB_REPO = sys.argv[1] if len(sys.argv) > 1 else "ShishirPatil/gorilla"
REPO_NAME = GITHUB_REPO.split("/")[-1]

session = requests.Session()
session.headers.update(GITEA_HEADERS)
github = GitHubRateLimiter()


def delete_if_exists():
    r = session.get(f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}", timeout=10)
    if r.status_code == 200:
        log.info("🗑️ حذف مخزن قبلی...")
        session.delete(
            f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}", timeout=10
        )
        time.sleep(3)


def method_1_migrate_code_only():
    log.info("📦 روش ۱: Migration فقط کد...")
    resp = session.post(
        f"{GITEA_API_BASE}/repos/migrate",
        json={
            "clone_addr": f"https://github.com/{GITHUB_REPO}.git",
            "auth_token": GITHUB_TOKEN,
            "mirror": False,
            "private": False,
            "repo_name": REPO_NAME,
            "repo_owner": GITEA_ORG,
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
    if resp.status_code in (200, 201):
        log.info(f"✅ کد منتقل شد")
        return True
    if resp.status_code == 409:
        log.info("✅ قبلاً وجود داره")
        return True
    log.warning(f"⚠️ روش ۱: {resp.status_code} — {resp.text[:300]}")
    return False


def method_2_mirror():
    log.info("🪞 روش ۲: Mirror...")
    delete_if_exists()
    resp = session.post(
        f"{GITEA_API_BASE}/repos/migrate",
        json={
            "clone_addr": f"https://github.com/{GITHUB_REPO}.git",
            "auth_token": GITHUB_TOKEN,
            "mirror": True,
            "mirror_interval": "10m",
            "private": False,
            "repo_name": REPO_NAME,
            "repo_owner": GITEA_ORG,
            "service": "github",
            "issues": False,
            "labels": False,
            "milestones": False,
            "pull_requests": False,
            "releases": False,
            "wiki": False,
            "lfs": False,
        },
        timeout=3600,
    )
    if resp.status_code in (200, 201):
        log.info("✅ Mirror ساخته شد")
        wait_for_sync()
        disable_mirror()
        return True
    log.warning(f"⚠️ روش ۲: {resp.status_code} — {resp.text[:300]}")
    return False


def wait_for_sync(max_wait=1800, interval=20):
    elapsed = 0
    while elapsed < max_wait:
        r = session.get(
            f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}", timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            size = data.get("size", 0)
            empty = data.get("empty", True)
            log.info(f"   ⏳ [{elapsed}s] size={size}KB empty={empty}")
            if not empty and size > 100:
                log.info("   ✅ Sync کامل شد!")
                return True
        time.sleep(interval)
        elapsed += interval
    return False


def disable_mirror():
    session.patch(
        f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}",
        json={"mirror": False},
        timeout=10,
    )
    log.info("✅ Mirror غیرفعال شد")


# ──────────────────────────────────────────
# Labels
# ──────────────────────────────────────────

def migrate_labels():
    log.info("🏷️ انتقال Labels...")
    label_map = {}
    page = 1

    while True:
        r = github.get(
            f"/repos/{GITHUB_REPO}/labels",
            params={"per_page": 100, "page": page},
        )
        if r.status_code != 200 or not r.json():
            break

        for lb in r.json():
            color = lb.get("color", "ee0701")
            if not color.startswith("#"):
                color = f"#{color}"

            gr = session.post(
                f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}/labels",
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
                existing = session.get(
                    f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}/labels",
                    params={"limit": 100},
                    timeout=10,
                )
                if existing.status_code == 200:
                    for el in existing.json():
                        if el["name"] == lb["name"]:
                            label_map[lb["name"]] = el["id"]
            time.sleep(0.2)
        page += 1

    log.info(f"   ✅ {len(label_map)} label")
    return label_map


# ──────────────────────────────────────────
# Issues
# ──────────────────────────────────────────

def migrate_issues(label_map, max_issues=500):
    log.info(f"🐛 انتقال Issues (max {max_issues})...")
    count = 0
    page = 1

    while count < max_issues:
        r = github.get(
            f"/repos/{GITHUB_REPO}/issues",
            params={
                "state": "all",
                "sort": "created",
                "direction": "asc",
                "per_page": 30,
                "page": page,
            },
        )
        if r.status_code != 200:
            break

        items = r.json()
        if not items:
            break

        for item in items:
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

            gr = session.post(
                f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}/issues",
                json={"title": item["title"], "body": full_body, "labels": ids},
                timeout=15,
            )

            if gr.status_code in (200, 201):
                gn = gr.json()["number"]
                count += 1

                # کامنت‌ها
                migrate_comments(
                    f"/repos/{GITHUB_REPO}/issues/{item['number']}/comments",
                    gn,
                )

                if state == "closed":
                    session.patch(
                        f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}"
                        f"/issues/{gn}",
                        json={"state": "closed"},
                        timeout=10,
                    )

                if count % 20 == 0:
                    log.info(f"   📊 Issues: {count} منتقل شد")

            time.sleep(0.3)

        page += 1
        time.sleep(1)

    log.info(f"   ✅ {count} Issue منتقل شد")
    return count


# ──────────────────────────────────────────
# Pull Requests
# ──────────────────────────────────────────

def migrate_pull_requests(label_map, max_prs=500):
    """
    انتقال Pull Requests از GitHub به Gitea
    چون Gitea API ساخت PR واقعی (با branch) رو نمیذاره،
    PRها رو به صورت Issue با برچسب [PR] ذخیره میکنیم
    + اطلاعات کامل diff و review
    """
    log.info(f"🔀 انتقال Pull Requests (max {max_prs})...")
    count = 0
    page = 1

    while count < max_prs:
        r = github.get(
            f"/repos/{GITHUB_REPO}/pulls",
            params={
                "state": "all",
                "sort": "created",
                "direction": "asc",
                "per_page": 30,
                "page": page,
            },
        )
        if r.status_code != 200:
            log.warning(f"   ⚠️ خطای API: {r.status_code}")
            break

        items = r.json()
        if not items:
            break

        for pr in items:
            if count >= max_prs:
                break

            success = create_pr_as_issue(pr, label_map)
            if success:
                count += 1

            if count % 20 == 0:
                log.info(f"   📊 PRs: {count} منتقل شد")

            time.sleep(0.5)

        page += 1
        time.sleep(1)

    log.info(f"   ✅ {count} Pull Request منتقل شد")
    return count


def create_pr_as_issue(pr: dict, label_map: dict) -> bool:
    """
    یک PR رو به صورت Issue غنی در Gitea ایجاد کن
    شامل: اطلاعات branch, merge status, diff, reviews, comments
    """
    number = pr.get("number", 0)
    title = pr.get("title", "")
    user = pr.get("user", {}).get("login", "?")
    body = pr.get("body", "") or ""
    state = pr.get("state", "open")
    merged = pr.get("merged_at") is not None
    created = pr.get("created_at", "")
    updated = pr.get("updated_at", "")
    head_branch = pr.get("head", {}).get("ref", "?")
    base_branch = pr.get("base", {}).get("ref", "?")
    html_url = pr.get("html_url", "")
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    changed_files_count = pr.get("changed_files", 0)

    # ── دریافت فایل‌های تغییریافته ──
    files_info = get_pr_files(number)

    # ── دریافت Review Comments ──
    reviews_info = get_pr_reviews(number)

    # ── ساخت بدنه غنی ──
    merge_status = "✅ Merged" if merged else ("❌ Closed" if state == "closed" else "🟡 Open")

    full_body = (
        f"## 🔀 Pull Request #{number}\n\n"
        f"| فیلد | مقدار |\n"
        f"|------|-------|\n"
        f"| **نویسنده** | @{user} |\n"
        f"| **تاریخ** | {created} |\n"
        f"| **وضعیت** | {merge_status} |\n"
        f"| **Branch** | `{head_branch}` → `{base_branch}` |\n"
        f"| **تغییرات** | +{additions} / -{deletions} در {changed_files_count} فایل |\n"
        f"| **GitHub** | [Link]({html_url}) |\n\n"
        f"---\n\n"
        f"### 📝 توضیحات\n\n{body}\n\n"
    )

    # اضافه کردن فایل‌های تغییریافته
    if files_info:
        full_body += "---\n\n### 📁 فایل‌های تغییریافته\n\n"
        for f in files_info:
            status_icon = {
                "added": "🟢", "removed": "🔴",
                "modified": "🟡", "renamed": "🔵",
            }.get(f["status"], "⚪")

            full_body += (
                f"#### {status_icon} `{f['filename']}` "
                f"(+{f['additions']} -{f['deletions']})\n\n"
            )

            if f.get("patch"):
                # محدود کردن سایز patch
                patch = f["patch"]
                if len(patch) > 3000:
                    patch = patch[:3000] + "\n... (truncated)"

                full_body += f"```diff\n{patch}\n```\n\n"

    # اضافه کردن Reviews
    if reviews_info:
        full_body += "---\n\n### 💬 Reviews\n\n"
        for rev in reviews_info:
            rev_icon = {
                "APPROVED": "✅",
                "CHANGES_REQUESTED": "🔴",
                "COMMENTED": "💬",
            }.get(rev["state"], "💬")

            full_body += (
                f"{rev_icon} **@{rev['user']}** — {rev['state']}\n\n"
                f"> {rev['body']}\n\n"
            )

    # ── Labels ──
    gh_labels = [lb["name"] for lb in pr.get("labels", [])]
    gitea_label_ids = [label_map[n] for n in gh_labels if n in label_map]

    # ── ساخت در Gitea ──
    pr_title = f"[PR #{number}] {title}"

    resp = session.post(
        f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}/issues",
        json={
            "title": pr_title,
            "body": full_body,
            "labels": gitea_label_ids,
        },
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        log.debug(f"   ⚠️ PR #{number} ساخته نشد: {resp.status_code}")
        return False

    gitea_number = resp.json()["number"]

    # ── کامنت‌های PR ──
    migrate_comments(
        f"/repos/{GITHUB_REPO}/issues/{number}/comments",
        gitea_number,
    )

    # ── بستن اگه بسته یا merge شده ──
    if state == "closed" or merged:
        session.patch(
            f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}"
            f"/issues/{gitea_number}",
            json={"state": "closed"},
            timeout=10,
        )

    return True


def get_pr_files(pr_number: int, max_files: int = 30) -> list[dict]:
    """دریافت فایل‌های تغییریافته PR"""
    r = github.get(
        f"/repos/{GITHUB_REPO}/pulls/{pr_number}/files",
        params={"per_page": max_files},
    )
    if r.status_code != 200:
        return []

    files = []
    for f in r.json():
        files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch": f.get("patch", ""),
        })

    time.sleep(0.3)
    return files


def get_pr_reviews(pr_number: int) -> list[dict]:
    """دریافت Reviews یک PR"""
    r = github.get(
        f"/repos/{GITHUB_REPO}/pulls/{pr_number}/reviews",
        params={"per_page": 20},
    )
    if r.status_code != 200:
        return []

    reviews = []
    for rev in r.json():
        body = rev.get("body", "") or ""
        if not body.strip():
            continue

        reviews.append({
            "user": rev.get("user", {}).get("login", "?"),
            "state": rev.get("state", "COMMENTED"),
            "body": body,
            "submitted_at": rev.get("submitted_at", ""),
        })

    time.sleep(0.3)
    return reviews


# ──────────────────────────────────────────
# Comments (مشترک بین Issues و PRs)
# ──────────────────────────────────────────

def migrate_comments(github_comments_url: str, gitea_issue_number: int):
    """انتقال کامنت‌ها"""
    r = github.get(github_comments_url, params={"per_page": 50})
    if r.status_code != 200:
        return

    for c in r.json():
        cu = c.get("user", {}).get("login", "?")
        cb = c.get("body", "")
        ct = c.get("created_at", "")

        session.post(
            f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}"
            f"/issues/{gitea_issue_number}/comments",
            json={"body": f"💬 *@{cu} — {ct}*\n\n---\n\n{cb}"},
            timeout=10,
        )
        time.sleep(0.2)


# ──────────────────────────────────────────
# Main
# ──────────────────────────────────────────

def main():
    log.info(f"🎯 مخزن: {GITHUB_REPO}")
    log.info(f"🏢 مقصد: {GITEA_ORG}/{REPO_NAME}")

    # بررسی آیا مخزن از قبل وجود داره
    r = session.get(
        f"{GITEA_API_BASE}/repos/{GITEA_ORG}/{REPO_NAME}", timeout=10
    )
    repo_exists = r.status_code == 200

    if repo_exists:
        data = r.json()
        log.info(f"📦 مخزن موجوده (size={data.get('size', 0)}KB)")
        log.info("فقط Issues و PRs منتقل می‌شوند")
    else:
        # ── کد ──
        log.info("\n" + "=" * 50)
        log.info("📦 مرحله ۱: انتقال کد")
        log.info("=" * 50)

        if not method_1_migrate_code_only():
            if not method_2_mirror():
                log.error("❌ انتقال کد ناموفق")
                sys.exit(1)

        log.info("⏳ صبر ۳۰ ثانیه...")
        time.sleep(30)

    # ── Labels ──
    log.info("\n" + "=" * 50)
    log.info("🏷️ مرحله ۲: انتقال Labels")
    log.info("=" * 50)
    label_map = migrate_labels()

    # ── Issues ──
    log.info("\n" + "=" * 50)
    log.info("🐛 مرحله ۳: انتقال Issues")
    log.info("=" * 50)
    issues_count = migrate_issues(label_map)

    # ── Pull Requests ──
    log.info("\n" + "=" * 50)
    log.info("🔀 مرحله ۴: انتقال Pull Requests")
    log.info("=" * 50)
    prs_count = migrate_pull_requests(label_map)

    # ── گزارش ──
    log.info("\n" + "=" * 50)
    log.info("📊 [bold green]گزارش نهایی[/]")
    log.info(f"   🏷️  Labels: {len(label_map)}")
    log.info(f"   🐛 Issues: {issues_count}")
    log.info(f"   🔀 PRs:    {prs_count}")
    log.info(f"   🔗 {GITEA_URL}/{GITEA_ORG}/{REPO_NAME}")
    log.info("=" * 50)


if __name__ == "__main__":
    main()