import sqlite3
from typing import List, Optional

from config.settings import DB_PATH, TELEGRAM_ADMIN_ID


def init_db():
    """Initializes the SQLite database and creates the necessary tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_videos (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_title TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id TEXT PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_channels (
            telegram_user_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (telegram_user_id, channel_id),
            FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id),
            FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
        )
    ''')
    _migrate_legacy_channels(cursor)
    conn.commit()
    conn.close()


def _migrate_legacy_channels(cursor):
    """Migrate old channels table (with added_by column) to user_channels."""
    cursor.execute("PRAGMA table_info(channels)")
    columns = {row[1] for row in cursor.fetchall()}
    if "added_by" not in columns:
        return

    cursor.execute("SELECT channel_id, channel_title, added_by FROM channels")
    rows = cursor.fetchall()
    for channel_id, channel_title, added_by in rows:
        cursor.execute(
            "INSERT OR IGNORE INTO channels (channel_id, channel_title) VALUES (?, ?)",
            (channel_id, channel_title),
        )
        owner_id = added_by
        if not owner_id or owner_id == "env":
            owner_id = TELEGRAM_ADMIN_ID
        if owner_id:
            cursor.execute(
                "INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)",
                (owner_id,),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO user_channels (telegram_user_id, channel_id) VALUES (?, ?)",
                (owner_id, channel_id),
            )

    cursor.execute('''
        CREATE TABLE channels_new (
            channel_id TEXT PRIMARY KEY,
            channel_title TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute(
        "INSERT OR IGNORE INTO channels_new (channel_id, channel_title, added_at) "
        "SELECT channel_id, channel_title, added_at FROM channels"
    )
    cursor.execute("DROP TABLE channels")
    cursor.execute("ALTER TABLE channels_new RENAME TO channels")
    # One-time: reclaim unowned rows created during legacy schema migration only.
    _assign_orphan_channels_to_admin(cursor)


def _assign_orphan_channels_to_admin(cursor):
    """Assign channels without subscribers to the admin (legacy migration helper)."""
    if not TELEGRAM_ADMIN_ID:
        return
    cursor.execute(
        '''
        SELECT c.channel_id
        FROM channels c
        LEFT JOIN user_channels uc ON uc.channel_id = c.channel_id
        WHERE uc.channel_id IS NULL
        '''
    )
    orphans = cursor.fetchall()
    if not orphans:
        return
    cursor.execute(
        "INSERT OR IGNORE INTO users (telegram_user_id) VALUES (?)",
        (TELEGRAM_ADMIN_ID,),
    )
    for (channel_id,) in orphans:
        cursor.execute(
            "INSERT OR IGNORE INTO user_channels (telegram_user_id, channel_id) VALUES (?, ?)",
            (TELEGRAM_ADMIN_ID, channel_id),
        )


def register_user(
    telegram_user_id: str,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
) -> bool:
    """Register or update a user. Returns True if this is a first-time registration."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM users WHERE telegram_user_id = ?",
        (telegram_user_id,),
    )
    is_new = cursor.fetchone() is None
    cursor.execute(
        '''
        INSERT INTO users (telegram_user_id, username, display_name)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            username = COALESCE(excluded.username, users.username),
            display_name = COALESCE(excluded.display_name, users.display_name)
        ''',
        (telegram_user_id, username, display_name),
    )
    conn.commit()
    conn.close()
    return is_new


def get_user(telegram_user_id: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT telegram_user_id, username, display_name, registered_at FROM users WHERE telegram_user_id = ?",
        (telegram_user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def channel_exists(channel_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM channels WHERE channel_id = ?", (channel_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def user_has_channel(telegram_user_id: str, channel_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM user_channels WHERE telegram_user_id = ? AND channel_id = ?",
        (telegram_user_id, channel_id),
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None


def upsert_channel(channel_id: str, channel_title: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO channels (channel_id, channel_title)
        VALUES (?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET channel_title = excluded.channel_title
        ''',
        (channel_id, channel_title),
    )
    conn.commit()
    conn.close()


def add_user_channel(telegram_user_id: str, channel_id: str, channel_title: str) -> bool:
    """Subscribe a user to a channel. Returns False if already subscribed."""
    if user_has_channel(telegram_user_id, channel_id):
        return False
    upsert_channel(channel_id, channel_title)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_channels (telegram_user_id, channel_id) VALUES (?, ?)",
        (telegram_user_id, channel_id),
    )
    conn.commit()
    conn.close()
    return True


def remove_user_channel(telegram_user_id: str, channel_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_channels WHERE telegram_user_id = ? AND channel_id = ?",
        (telegram_user_id, channel_id),
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def list_user_channels(telegram_user_id: str) -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT c.channel_id, c.channel_title, uc.added_at
        FROM user_channels uc
        JOIN channels c ON c.channel_id = uc.channel_id
        WHERE uc.telegram_user_id = ?
        ORDER BY uc.added_at
        ''',
        (telegram_user_id,),
    )
    channels = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return channels


def get_channel_subscribers(channel_id: str) -> List[str]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT telegram_user_id FROM user_channels WHERE channel_id = ? ORDER BY added_at",
        (channel_id,),
    )
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


def get_all_channel_ids() -> List[str]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT channel_id FROM user_channels ORDER BY channel_id"
    )
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


def seed_channels_from_env(channel_ids: List[str], admin_user_id: str, resolve_title=None):
    """Bootstrap CHANNEL_IDS onto the admin once (empty subscription list only).

    After the admin has any subscriptions, later restarts/deploys must not
    re-add env defaults — that would overwrite intentional /remove or /add edits.
    """
    if not admin_user_id or not channel_ids:
        return
    register_user(admin_user_id, display_name="Admin (env seed)")
    if list_user_channels(admin_user_id):
        return
    for channel_id in channel_ids:
        title = channel_id
        if resolve_title:
            info = resolve_title(channel_id)
            if info:
                title = info.get("channel_title", channel_id)
        add_user_channel(admin_user_id, channel_id, title)


def is_video_processed(video_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_videos WHERE video_id = ?", (video_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def mark_video_processed(video_id: str, channel_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT OR IGNORE INTO processed_videos (video_id, channel_id)
        VALUES (?, ?)
        ''',
        (video_id, channel_id),
    )
    conn.commit()
    conn.close()


def get_system_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM channels")
    channel_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM user_channels")
    subscription_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM processed_videos")
    processed_video_count = cursor.fetchone()[0]
    conn.close()
    return {
        "user_count": user_count,
        "channel_count": channel_count,
        "subscription_count": subscription_count,
        "processed_video_count": processed_video_count,
    }


def list_all_users() -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT
            u.telegram_user_id,
            u.username,
            u.display_name,
            u.registered_at,
            COUNT(uc.channel_id) AS channel_count
        FROM users u
        LEFT JOIN user_channels uc ON uc.telegram_user_id = u.telegram_user_id
        GROUP BY u.telegram_user_id
        ORDER BY u.registered_at
        '''
    )
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users
