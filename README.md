# PostgreSQL Backup → Cloudflare R2

Ежедневный backup базы данных PostgreSQL `DB_name` с автоматической отправкой в Cloudflare R2 (bucket `your bucket name`, папка `backup/`).

## Возможности

- `pg_dump` → `zip -P` (сжатие с паролем) → загрузка в R2
- Ротация: максимум 60 копий, удаление старейших
- CLI для управления backup'ами

## Установка

Зависимости уже установлены: `python3`, `boto3`, `pg_dump`, `zip`/`unzip`.

## Использование

```bash
cd /home/informaten/pg-backup

# Создать backup прямо сейчас
python3 backup.py backup

# Список всех backup'ов в R2
python3 backup.py list

# Статус: кол-во копий, старейшая, новейшая
python3 backup.py status

# Скачать backup локально
python3 backup.py download DB name.zip -o /tmp

# Восстановить БД из backup'а
python3 backup.py restore DB name.zip

# Удалить старые копии (если > 60)
python3 backup.py cleanup

# Установить cron (ежедневно в 03:00)
python3 backup.py install-cron

# Установить cron на другое время (05:30)
python3 backup.py install-cron --hour 5 --minute 30
```

## Конфигурация

Все настройки в `.env`:

```
DATABASE_URL=postgresql://user:pass@host:port/dbname
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=vega
R2_ENDPOINT=https://....r2.cloudflarestorage.com
R2_REGION=auto
BACKUP_PASSWORD=renovatio
MAX_BACKUPS=60
DB_NAME=DB_name
R2_BACKUP_PREFIX=backup/
```

## Cron

Cron задача запускается каждый день в 03:00:

```
0 3 * * * /usr/bin/python3 /home/informaten/pg-backup/backup.py backup >> /home/informaten/pg-backup/backup.log 2>&1
```

Логи: `/home/informaten/pg-backup/backup.log`

## Структура в R2

```
project/
└── backup/
    ├── DB name.zip
    ├── DB name.zip
    └── ...  (макс. 60 файлов)
```

## Восстановление

```bash
# Скачать и восстановить
python3 backup.py restore DB name.zip

# Или вручную: скачать, разархивировать, загрузить
python3 backup.py download DB name.zip -o /tmp
unzip -P project /tmp/DB name.zip -d /tmp
psql -h localhost -U username -d DB_name -f /tmp/DB name.sql
```
