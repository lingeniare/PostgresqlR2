#!/usr/bin/env python3
"""
PostgreSQL backup to Cloudflare R2 with password-protected zip compression.
CLI tool for managing automated daily backups with rotation (max 60 copies).
"""

import argparse
import os
import sys
import subprocess
import tempfile
import logging
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"


def load_env(path: Path):
    """Parse a .env file and set environment variables."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


load_env(ENV_FILE)

DATABASE_URL = os.getenv("DATABASE_URL", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "vega")
R2_REGION = os.getenv("R2_REGION", "auto")
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "")
BACKUP_PASSWORD = os.getenv("BACKUP_PASSWORD", "renovatio")
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "60"))
DB_NAME = os.getenv("DB_NAME", "fishandrole")
R2_BACKUP_PREFIX = os.getenv("R2_BACKUP_PREFIX", "backup/")

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("pg-backup")


# ---------------------------------------------------------------------------
# R2 client
# ---------------------------------------------------------------------------

def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name=R2_REGION,
        config=Config(signature_version="s3v4"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_db_url(url: str):
    """Parse postgresql://user:pass@host:port/dbname into components."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return {
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "dbname": parsed.path.lstrip("/"),
    }


def generate_backup_name() -> str:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return f"{DB_NAME}_{ts}"


def list_backups(client) -> list:
    """List all backup objects in R2, sorted oldest → newest."""
    paginator = client.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=R2_BACKUP_PREFIX):
        for obj in page.get("Contents", []):
            objects.append(obj)
    objects.sort(key=lambda o: o["Key"])
    return objects


def delete_backup(client, key: str):
    client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
    log.info(f"Deleted old backup: {key}")


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def do_backup():
    """Create a backup, zip it with password, upload to R2, rotate."""
    db = parse_db_url(DATABASE_URL)
    backup_name = generate_backup_name()
    r2_key = f"{R2_BACKUP_PREFIX}{backup_name}.zip"

    log.info(f"Starting backup of database '{db['dbname']}'...")

    with tempfile.TemporaryDirectory() as tmpdir:
        sql_path = os.path.join(tmpdir, f"{backup_name}.sql")
        zip_path = os.path.join(tmpdir, f"{backup_name}.zip")

        # 1. pg_dump
        env = os.environ.copy()
        env["PGPASSWORD"] = db["password"]
        cmd = [
            "pg_dump",
            "-h", db["host"],
            "-p", str(db["port"]),
            "-U", db["user"],
            "-d", db["dbname"],
            "-F", "p",
            "-f", sql_path,
        ]
        log.info(f"Running pg_dump → {sql_path}")
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            log.error(f"pg_dump failed:\n{result.stderr}")
            sys.exit(1)

        # 2. zip with password
        log.info(f"Compressing with zip (password-protected) → {zip_path}")
        result = subprocess.run(
            ["zip", "-j", "-P", BACKUP_PASSWORD, zip_path, sql_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.error(f"zip failed:\n{result.stderr}")
            sys.exit(1)

        # 3. Upload to R2
        client = get_r2_client()
        file_size = os.path.getsize(zip_path)
        log.info(f"Uploading to R2: {r2_key} ({file_size / 1024 / 1024:.1f} MB)")
        client.upload_file(zip_path, R2_BUCKET_NAME, r2_key)
        log.info(f"Upload complete: {r2_key}")

    # 4. Rotate
    do_cleanup(client)

    log.info("Backup finished successfully.")


def do_list():
    """List all backups in R2."""
    client = get_r2_client()
    backups = list_backups(client)
    if not backups:
        print("No backups found.")
        return

    print(f"{'#':<4} {'Filename':<40} {'Size':>12} {'Last Modified'}")
    print("-" * 80)
    for i, obj in enumerate(backups, 1):
        name = os.path.basename(obj["Key"])
        size_mb = obj["Size"] / 1024 / 1024
        modified = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
        print(f"{i:<4} {name:<40} {size_mb:>10.1f}MB {modified}")
    print(f"\nTotal: {len(backups)} / {MAX_BACKUPS} backups")


def do_status():
    """Show backup count, oldest and newest."""
    client = get_r2_client()
    backups = list_backups(client)
    if not backups:
        print("No backups found.")
        return

    oldest = backups[0]
    newest = backups[-1]
    total_size = sum(o["Size"] for o in backups) / 1024 / 1024

    print(f"Backups:  {len(backups)} / {MAX_BACKUPS}")
    print(f"Total:    {total_size:.1f} MB")
    print(f"Oldest:   {os.path.basename(oldest['Key'])}  ({oldest['LastModified'].strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"Newest:   {os.path.basename(newest['Key'])}  ({newest['LastModified'].strftime('%Y-%m-%d %H:%M:%S')})")


def do_cleanup(client=None):
    """Delete oldest backups if count exceeds MAX_BACKUPS."""
    if client is None:
        client = get_r2_client()
    backups = list_backups(client)
    excess = len(backups) - MAX_BACKUPS
    if excess <= 0:
        log.info(f"Cleanup: {len(backups)}/{MAX_BACKUPS} backups — no action needed.")
        return

    log.info(f"Cleanup: {len(backups)} backups, removing {excess} oldest...")
    for obj in backups[:excess]:
        delete_backup(client, obj["Key"])
    log.info(f"Cleanup done. {len(backups) - excess} backups remaining.")


def do_download(filename: str, output_dir: str = "."):
    """Download a backup from R2 to local directory."""
    client = get_r2_client()
    key = f"{R2_BACKUP_PREFIX}{filename}" if not filename.startswith(R2_BACKUP_PREFIX) else filename
    output_path = os.path.join(output_dir, os.path.basename(key))

    log.info(f"Downloading {key} → {output_path}")
    client.download_file(R2_BUCKET_NAME, key, output_path)
    log.info(f"Downloaded: {output_path}")
    print(f"Saved to: {output_path}")


def do_restore(filename: str):
    """Download a backup from R2, unzip, and restore to database."""
    db = parse_db_url(DATABASE_URL)
    client = get_r2_client()
    key = f"{R2_BACKUP_PREFIX}{filename}" if not filename.startswith(R2_BACKUP_PREFIX) else filename

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, os.path.basename(key))
        log.info(f"Downloading {key}...")
        client.download_file(R2_BUCKET_NAME, key, zip_path)

        # Unzip with password
        log.info(f"Extracting (password-protected)...")
        result = subprocess.run(
            ["unzip", "-o", "-P", BACKUP_PASSWORD, zip_path, "-d", tmpdir],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.error(f"unzip failed:\n{result.stderr}")
            sys.exit(1)

        # Find the .sql file
        sql_files = list(Path(tmpdir).glob("*.sql"))
        if not sql_files:
            log.error("No .sql file found in archive.")
            sys.exit(1)
        sql_path = str(sql_files[0])

        # Restore via psql
        env = os.environ.copy()
        env["PGPASSWORD"] = db["password"]
        log.info(f"Restoring to database '{db['dbname']}'...")
        cmd = [
            "psql",
            "-h", db["host"],
            "-p", str(db["port"]),
            "-U", db["user"],
            "-d", db["dbname"],
            "-f", sql_path,
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            log.error(f"psql restore failed:\n{result.stderr}")
            sys.exit(1)

    log.info("Restore completed successfully.")


def do_install_cron(hour: int = 3, minute: int = 0):
    """Install a cron job for daily backup."""
    script = str(SCRIPT_DIR / "backup.py")
    log_file = str(SCRIPT_DIR / "backup.log")
    cron_line = f"{minute} {hour} * * * /usr/bin/python3 {script} backup >> {log_file} 2>&1"

    # Read existing crontab
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout.strip() if result.returncode == 0 else ""

    # Remove old entries for this script
    lines = [l for l in existing.splitlines() if "backup.py backup" not in l]
    lines.append(cron_line)
    new_crontab = "\n".join(lines) + "\n"

    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    log.info(f"Cron job installed: daily at {hour:02d}:{minute:02d}")
    log.info(f"  {cron_line}")
    print(f"Cron installed: daily at {hour:02d}:{minute:02d}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PostgreSQL backup to Cloudflare R2 with rotation.",
        prog="backup.py",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("backup", help="Create a backup now and upload to R2")
    sub.add_parser("list", help="List all backups in R2")
    sub.add_parser("status", help="Show backup count, oldest and newest")
    sub.add_parser("cleanup", help="Delete old backups if count exceeds MAX_BACKUPS")

    p_download = sub.add_parser("download", help="Download a backup from R2")
    p_download.add_argument("filename", help="Backup filename (e.g. fishandrole_2025-07-27_030000.zip)")
    p_download.add_argument("-o", "--output", default=".", help="Output directory (default: current)")

    p_restore = sub.add_parser("restore", help="Download and restore a backup to the database")
    p_restore.add_argument("filename", help="Backup filename (e.g. fishandrole_2025-07-27_030000.zip)")

    p_cron = sub.add_parser("install-cron", help="Install cron job for daily backup")
    p_cron.add_argument("--hour", type=int, default=3, help="Hour (0-23, default: 3)")
    p_cron.add_argument("--minute", type=int, default=0, help="Minute (0-59, default: 0)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "backup":
        do_backup()
    elif args.command == "list":
        do_list()
    elif args.command == "status":
        do_status()
    elif args.command == "cleanup":
        do_cleanup()
    elif args.command == "download":
        do_download(args.filename, args.output)
    elif args.command == "restore":
        do_restore(args.filename)
    elif args.command == "install-cron":
        do_install_cron(args.hour, args.minute)


if __name__ == "__main__":
    main()
