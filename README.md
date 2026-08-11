# PostgreSQL Backup → Cloudflare R2

Daily PostgreSQL database backup (`DB_name`) with automatic upload to Cloudflare R2 (bucket `your bucket name`, folder `backup/`).

## Features

- `pg_dump` → `zip -P` (password-protected compression) → upload to R2
- Rotation: maximum 60 copies, oldest deleted first
- CLI for backup management

## Installation

Dependencies already installed: `python3`, `boto3`, `pg_dump`, `zip`/`unzip`.

## Usage

```bash
cd /home/informaten/pg-backup

# Create a backup right now
python3 backup.py backup

# List all backups in R2
python3 backup.py list

# Status: number of copies, oldest, newest
python3 backup.py status

# Download a backup locally
python3 backup.py download DB name.zip -o /tmp

# Restore the database from a backup
python3 backup.py restore DB name.zip

# Delete old copies (if > 60)
python3 backup.py cleanup

# Install cron (daily at 03:00)
python3 backup.py install-cron

# Install cron at a different time (05:30)
python3 backup.py install-cron --hour 5 --minute 30
```

## Configuration

All settings are in `.env`:

```
DATABASE_URL=postgresql://user:pass@host:port/dbname
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=..
R2_ENDPOINT=https://....r2.cloudflarestorage.com
R2_REGION=auto
BACKUP_PASSWORD=..
MAX_BACKUPS=60
DB_NAME=DB_name
R2_BACKUP_PREFIX=backup/
```

## Cron

The cron job runs every day at 03:00:

```
0 3 * * * /usr/bin/python3 /home/informaten/pg-backup/backup.py backup >> /home/informaten/pg-backup/backup.log 2>&1
```

Logs: `/home/informaten/pg-backup/backup.log`

## R2 Structure

```
project/
└── backup/
    ├── DB name.zip
    ├── DB name.zip
    └── ...  (max. 60 files)
```

## Restore

```bash
# Download and restore
python3 backup.py restore DB name.zip

# Or manually: download, unzip, load
python3 backup.py download DB name.zip -o /tmp
unzip -P project /tmp/DB name.zip -d /tmp
psql -h localhost -U username -d DB_name -f /tmp/DB name.sql
```
