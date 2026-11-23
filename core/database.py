import sqlite3
import re
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = "ghostlink.db"

ALLOWED_THEMES = {"midnight", "violet", "aurora"}
ALLOWED_REACTIONS = {"❤️", "🔥", "😂", "👀", "💜"}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            bio TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            theme TEXT DEFAULT 'violet',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            reaction TEXT DEFAULT '',
            is_reported INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS friends (
            user_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, friend_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (friend_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient_id, created_at DESC);
        """
    )
    # Safe migration if upgrading from an older ghostlink.db without avatar_url
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "avatar_url" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def validate_username(username: str) -> tuple[bool, str]:
    if not username or len(username) < 3 or len(username) > 24:
        return False, "Username must be between 3 and 24 characters."
    if not re.match(r"^[a-zA-Z0-9_]+$", username):
        return False, "Username can only contain letters, numbers, and underscores."
    reserved = {"admin", "api", "auth", "dashboard", "login", "logout", "register", "live", "u", "static", "explore"}
    if username.lower() in reserved:
        return False, "That username is reserved."
    return True, ""


def create_user(username: str, display_name: str, password: str) -> tuple[bool, str | int]:
    username = username.strip()
    display_name = (display_name or username).strip()[:40]
    valid, err = validate_username(username)
    if not valid:
        return False, err
    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters."

    conn = get_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO users (username, display_name, password_hash, bio, avatar_url, theme, created_at)
            VALUES (?, ?, ?, ?, '', 'violet', ?)
            """,
            (
                username.lower(),
                display_name,
                generate_password_hash(password),
                "Send me an anonymous message — zero identity tracking.",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return True, cur.lastrowid
    except sqlite3.IntegrityError:
        return False, "Username is already taken."
    finally:
        conn.close()


def authenticate_user(username: str, password: str):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
    conn.close()
    if user and check_password_hash(user["password_hash"], password):
        return dict(user)
    return None


def get_user_by_id(user_id: int):
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, display_name, bio, avatar_url, theme, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_by_username(username: str):
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, display_name, bio, avatar_url, theme FROM users WHERE username = ?",
        (username.strip().lower(),),
    ).fetchone()
    conn.close()
    return dict(user) if user else None


def update_user_profile(
    user_id: int,
    new_username: str,
    display_name: str,
    bio: str,
    theme: str,
    avatar_url: str | None = None,
) -> tuple[bool, str]:
    new_username = new_username.strip().lower()
    valid, err = validate_username(new_username)
    if not valid:
        return False, err

    display_name = display_name.strip()[:40]
    if not display_name:
        return False, "Display name cannot be empty."

    bio = bio.strip()[:160]
    theme = theme.lower() if theme and theme.lower() in ALLOWED_THEMES else "violet"

    conn = get_db()
    try:
        if avatar_url is not None:
            conn.execute(
                """
                UPDATE users
                SET username = ?, display_name = ?, bio = ?, theme = ?, avatar_url = ?
                WHERE id = ?
                """,
                (new_username, display_name, bio, theme, avatar_url, user_id),
            )
        else:
            conn.execute(
                """
                UPDATE users
                SET username = ?, display_name = ?, bio = ?, theme = ?
                WHERE id = ?
                """,
                (new_username, display_name, bio, theme, user_id),
            )
        conn.commit()
        return True, "Profile updated successfully."
    except sqlite3.IntegrityError:
        return False, "That username is already taken by another user."
    finally:
        conn.close()


# -------------------- FRIEND & DISCOVERY SYSTEM --------------------

def search_users(query: str, exclude_user_id: int | None = None, limit: int = 20) -> list[dict]:
    conn = get_db()
    q = f"%{query.strip()}%"
    if query.strip():
        rows = conn.execute(
            """
            SELECT id, username, display_name, bio, avatar_url, theme
            FROM users
            WHERE (username LIKE ? OR display_name LIKE ?)
              AND (? IS NULL OR id != ?)
            ORDER BY username ASC
            LIMIT ?
            """,
            (q, q, exclude_user_id, exclude_user_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, username, display_name, bio, avatar_url, theme
            FROM users
            WHERE (? IS NULL OR id != ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (exclude_user_id, exclude_user_id, limit),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_friends(user_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT u.id, u.username, u.display_name, u.bio, u.avatar_url, u.theme, f.created_at AS added_at
        FROM friends f
        JOIN users u ON u.id = f.friend_id
        WHERE f.user_id = ?
        ORDER BY f.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_friend(user_id: int, target_id: int) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?",
        (user_id, target_id),
    ).fetchone()
    conn.close()
    return row is not None


def add_friend(user_id: int, target_username: str) -> tuple[bool, str]:
    target = get_user_by_username(target_username)
    if not target:
        return False, "User not found."
    if target["id"] == user_id:
        return False, "You cannot add yourself as a friend."

    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO friends (user_id, friend_id, created_at) VALUES (?, ?, ?)",
            (user_id, target["id"], datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True, f"Added @{target['username']} to your friends."
    finally:
        conn.close()


def remove_friend(user_id: int, target_username: str) -> tuple[bool, str]:
    target = get_user_by_username(target_username)
    if not target:
        return False, "User not found."
    conn = get_db()
    conn.execute("DELETE FROM friends WHERE user_id = ? AND friend_id = ?", (user_id, target["id"]))
    conn.commit()
    conn.close()
    return True, f"Removed @{target['username']} from friends."


# -------------------- ANONYMOUS MESSAGES --------------------

def create_anonymous_message(recipient_id: int, content: str) -> tuple[bool, str]:
    cleaned = content.strip()
    if not cleaned:
        return False, "Message cannot be empty."
    if len(cleaned) > 500:
        return False, "Message exceeds the 500-character limit."

    conn = get_db()
    conn.execute(
        """
        INSERT INTO messages (recipient_id, content, is_read, reaction, is_reported, created_at)
        VALUES (?, ?, 0, '', 0, ?)
        """,
        (recipient_id, cleaned, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return True, "Anonymous message delivered."


def get_inbox_data(user_id: int) -> dict:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, content, is_read, reaction, is_reported, created_at
        FROM messages
        WHERE recipient_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()

    messages = [dict(r) for r in rows]
    total_count = len(messages)
    unread_count = sum(1 for m in messages if m["is_read"] == 0)
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_count = sum(1 for m in messages if m["created_at"].startswith(today_prefix))

    return {
        "messages": messages,
        "stats": {
            "total": total_count,
            "unread": unread_count,
            "today": today_count,
        },
    }


def mark_message_read(user_id: int, message_id: int) -> bool:
    conn = get_db()
    cur = conn.execute(
        "UPDATE messages SET is_read = 1 WHERE id = ? AND recipient_id = ?",
        (message_id, user_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def mark_all_read(user_id: int):
    conn = get_db()
    conn.execute("UPDATE messages SET is_read = 1 WHERE recipient_id = ?", (user_id,))
    conn.commit()
    conn.close()


def delete_message(user_id: int, message_id: int) -> bool:
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM messages WHERE id = ? AND recipient_id = ?",
        (message_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def set_message_reaction(user_id: int, message_id: int, emoji: str) -> tuple[bool, str]:
    if emoji and emoji not in ALLOWED_REACTIONS:
        return False, "Unsupported reaction."
    conn = get_db()
    row = conn.execute(
        "SELECT reaction FROM messages WHERE id = ? AND recipient_id = ?",
        (message_id, user_id),
    ).fetchone()
    if not row:
        conn.close()
        return False, "Message not found."

    new_reaction = "" if row["reaction"] == emoji else emoji
    conn.execute(
        "UPDATE messages SET reaction = ?, is_read = 1 WHERE id = ? AND recipient_id = ?",
        (new_reaction, message_id, user_id),
    )
    conn.commit()
    conn.close()
    return True, new_reaction


def report_message(user_id: int, message_id: int) -> bool:
    conn = get_db()
    cur = conn.execute(
        "UPDATE messages SET is_reported = 1, is_read = 1 WHERE id = ? AND recipient_id = ?",
        (message_id, user_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated