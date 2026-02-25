"""
زمان‌بند دوره‌ای
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime

import schedule

from config.settings import CRON_INTERVAL_HOURS, PROJECTS_PER_KEYWORD, SEARCH_KEYWORDS
from core.data_extractor import DataExtractor
from core.gitea_migrator import GiteaMigrator
from core.github_crawler import GitHubCrawler
from models.repository import RepositoryDB
from utils.logger import log


class CronManager:
    """اجرای زمان‌بندی‌شده پایپلاین"""

    def __init__(self):
        self.db = RepositoryDB()
        self.crawler = GitHubCrawler(self.db)
        self.extractor = DataExtractor(self.db, self.crawler.rate_limiter)
        self.migrator = GiteaMigrator(self.db)
        self._running = True

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        log.info("\n🛑 خاموشی ایمن...")
        self._running = False
        sys.exit(0)

    def run_full_pipeline(self) -> None:
        """کرول → اعتبارسنجی → استخراج → انتقال"""
        start = datetime.utcnow()
        log.info("\n" + "=" * 60)
        log.info(
            f"🚀 [bold magenta]شروع پایپلاین[/] — "
            f"{start.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        log.info(
            f"📋 هدف: {PROJECTS_PER_KEYWORD} پروژه × "
            f"{len(SEARCH_KEYWORDS)} کلیدواژه = "
            f"{PROJECTS_PER_KEYWORD * len(SEARCH_KEYWORDS)} پروژه"
        )
        log.info("=" * 60)

        try:
            # ── Rate Limit ──
            self.crawler.rate_limiter.check_rate_limit()

            # ── مرحله ۱: کرول + اعتبارسنجی ──
            log.info("\n📡 [bold]مرحله ۱: کرول و اعتبارسنجی[/]")
            valid_repos = self.crawler.search_repositories()

            # ── مرحله ۲: استخراج کامل ──
            log.info("\n📥 [bold]مرحله ۲: استخراج داده‌های آموزشی[/]")
            for i, repo in enumerate(valid_repos, 1):
                log.info(f"\n── [{i}/{len(valid_repos)}] ──")
                self.extractor.extract_all(repo)
                time.sleep(1)

            # ── مرحله ۳: انتقال ──
            log.info("\n🚀 [bold]مرحله ۳: انتقال به Gitea[/]")
            migration = self.migrator.migrate_all_pending()

            # ── گزارش ──
            elapsed = (datetime.utcnow() - start).total_seconds()
            stats = self.db.get_stats()

            log.info("\n" + "=" * 60)
            log.info("📊 [bold green]گزارش نهایی[/]")
            log.info(f"   ⏱️  مدت اجرا: {elapsed:.0f} ثانیه ({elapsed/60:.1f} دقیقه)")
            log.info(f"   🔍 پروژه‌های واجد شرایط: {len(valid_repos)}")
            log.info(f"   ✅ انتقال موفق: {migration['success']}")
            log.info(f"   ❌ انتقال ناموفق: {migration['failed']}")
            log.info(f"   🗄️  کل در دیتابیس: {stats['total_repos']}")
            log.info(f"   ✅ آماده آموزش: {stats['training_ready']}")
            log.info(f"   ⛔ رد شده: {stats['rejected']}")
            log.info(f"   📄 رکوردهای استخراج‌شده: {stats['total_extracted_records']}")
            if stats["extracted_by_type"]:
                for dtype, count in stats["extracted_by_type"].items():
                    log.info(f"      {dtype}: {count}")
            log.info("=" * 60)

        except Exception as e:
            log.exception(f"❌ خطای پایپلاین: {e}")

    def start_scheduler(self) -> None:
        """شروع زمان‌بند"""
        log.info(f"⏰ زمان‌بند: هر {CRON_INTERVAL_HOURS} ساعت")

        self.run_full_pipeline()

        schedule.every(CRON_INTERVAL_HOURS).hours.do(self.run_full_pipeline)
        log.info(f"⏰ اجرای بعدی: {schedule.next_run()}")

        while self._running:
            schedule.run_pending()
            time.sleep(30)