"""
Delete analysis outputs (media/output/<run>/…) older than
settings.OUTPUT_RETENTION_DAYS, and email settings.ADMINS a warning if
storage is running low.

Intended to run once a day via cron, e.g.:

    0 3 * * * cd /path/to/targeting_project && \
        /path/to/venv/bin/python manage.py cleanup_old_outputs \
        >> /var/log/targeting_tools/cleanup.log 2>&1

Each analysis run writes its results to its own subfolder under
MEDIA_ROOT/output/ (the folder name is a per-run token, e.g. a UUID or
timestamp — see land_suitability.py / land_similarity.py / land_statistics.py
`ras_temp_path`). A run's "age" is based on the most recently modified file
inside its folder, not the folder's own mtime, since only the files reliably
reflect when the analysis actually produced output.

Storage alerting fires independently of whether anything was deleted this
run — it always reports current usage, and only emails admins when free
space is low, so it's a live storage check, not just a consequence of
cleanup.

Run with --dry-run to see what would be deleted without touching anything.
"""
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.mail import mail_admins
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def _dir_size_bytes(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _run_folder_age_days(run_path):
    """Age in days, based on the newest file inside the folder (falls back
    to the folder's own mtime if it's empty)."""
    newest = None
    for dirpath, _dirnames, filenames in os.walk(run_path):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    if newest is None:
        try:
            newest = os.path.getmtime(run_path)
        except OSError:
            return 0
    age_seconds = time.time() - newest
    return age_seconds / 86400.0


class Command(BaseCommand):
    help = (
        'Delete analysis output folders older than OUTPUT_RETENTION_DAYS '
        'and email admins if storage is running low.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be deleted without deleting anything.',
        )
        parser.add_argument(
            '--retention-days', type=float, default=None,
            help='Override settings.OUTPUT_RETENTION_DAYS for this run.',
        )

    def handle(self, *args, **options):
        retention_days = options['retention_days']
        if retention_days is None:
            retention_days = getattr(settings, 'OUTPUT_RETENTION_DAYS', 14)
        dry_run = options['dry_run']

        output_root = os.path.join(settings.MEDIA_ROOT, 'output')
        if not os.path.isdir(output_root):
            self.stdout.write(f'No output directory at {output_root} — nothing to do.')
            return

        cutoff_desc = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted_count = 0
        kept_count = 0
        bytes_freed = 0

        for entry in sorted(os.listdir(output_root)):
            run_path = os.path.join(output_root, entry)
            if not os.path.isdir(run_path):
                continue
            age_days = _run_folder_age_days(run_path)
            if age_days >= retention_days:
                size = _dir_size_bytes(run_path)
                self.stdout.write(
                    f'{"[dry-run] would delete" if dry_run else "Deleting"} '
                    f'{entry} (age {age_days:.1f}d, {size / 1e6:.1f} MB)'
                )
                if not dry_run:
                    try:
                        shutil.rmtree(run_path)
                    except OSError as e:
                        logger.exception('Failed to remove %s', run_path)
                        self.stderr.write(f'  could not remove {entry}: {e}')
                        continue
                deleted_count += 1
                bytes_freed += size
            else:
                kept_count += 1

        self.stdout.write(
            f'Cleanup {"(dry run) " if dry_run else ""}complete: '
            f'{deleted_count} run(s) removed '
            f'({bytes_freed / 1e6:.1f} MB freed), {kept_count} run(s) kept '
            f'(retention: {retention_days:g} days, cutoff ~{cutoff_desc:%Y-%m-%d}).'
        )

        self._check_storage_and_alert(output_root)

    def _check_storage_and_alert(self, output_root):
        """Always logs current usage; emails ADMINS only when free space is
        below the configured threshold, so storage pressure is caught
        between cleanup runs rather than discovered when the disk is full."""
        try:
            usage = shutil.disk_usage(settings.MEDIA_ROOT)
        except OSError:
            logger.exception('Could not read disk usage for %s', settings.MEDIA_ROOT)
            return

        free_percent = (usage.free / usage.total * 100) if usage.total else 100
        output_size = _dir_size_bytes(output_root)
        threshold = getattr(settings, 'STORAGE_ALERT_FREE_PERCENT_THRESHOLD', 10)

        summary = (
            f'Disk: {usage.total / 1e9:.1f} GB total, '
            f'{usage.used / 1e9:.1f} GB used, '
            f'{usage.free / 1e9:.1f} GB free ({free_percent:.1f}% free). '
            f'media/output/ alone: {output_size / 1e9:.2f} GB.'
        )
        self.stdout.write(summary)
        logger.info('Storage check — %s', summary)

        if free_percent < threshold:
            subject = (
                f'[Targeting Tools] Low disk space: {free_percent:.1f}% free'
            )
            message = (
                f'Free disk space on the media volume has dropped below the '
                f'{threshold:g}% alert threshold.\n\n{summary}\n\n'
                f'Output files are cleaned up automatically after '
                f'{getattr(settings, "OUTPUT_RETENTION_DAYS", 14):g} days, but '
                f'that may not be fast enough if storage keeps filling up. '
                f'Consider investigating large/unexpected files under '
                f'{output_root}, or reducing OUTPUT_RETENTION_DAYS.'
            )
            if getattr(settings, 'ADMINS', None):
                try:
                    mail_admins(subject, message, fail_silently=False)
                    self.stdout.write(self.style.WARNING(
                        f'Free space {free_percent:.1f}% is below the '
                        f'{threshold:g}% threshold — alert emailed to admins.'
                    ))
                except Exception:
                    logger.exception('Failed to email admins about low storage')
                    self.stderr.write(self.style.ERROR(
                        'Free space is low but the alert email could not be sent '
                        '— check ADMINS/EMAIL_* settings.'
                    ))
            else:
                self.stdout.write(self.style.WARNING(
                    f'Free space {free_percent:.1f}% is below the {threshold:g}% '
                    f'threshold, but settings.ADMINS is empty — no alert email sent. '
                    f'Add administrators to ADMINS in data.json to enable alerts.'
                ))
