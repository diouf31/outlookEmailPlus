from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from outlook_web.db import get_db
from outlook_web.security.auth import get_current_user_id


def get_tags() -> List[Dict]:
    """获取当前用户的标签"""
    db = get_db()
    user_id = get_current_user_id()
    if user_id:
        cursor = db.execute(
            "SELECT * FROM tags WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
    else:
        cursor = db.execute("SELECT * FROM tags ORDER BY created_at DESC")
    return [dict(row) for row in cursor.fetchall()]


def add_tag(name: str, color: str) -> Optional[int]:
    """添加标签（自动关联当前用户）"""
    db = get_db()
    owner_user_id = get_current_user_id() or 1
    try:
        cursor = db.execute(
            "INSERT INTO tags (name, color, user_id) VALUES (?, ?, ?)",
            (name, color, owner_user_id),
        )
        db.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None


def delete_tag(tag_id: int) -> bool:
    """删除标签（含 user_id 验证）"""
    db = get_db()
    user_id = get_current_user_id()
    if user_id:
        cursor = db.execute("DELETE FROM tags WHERE id = ? AND user_id = ?", (tag_id, user_id))
    else:
        cursor = db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    db.commit()
    return cursor.rowcount > 0


def get_account_tags(account_id: int) -> List[Dict]:
    """获取账号的标签"""
    db = get_db()
    cursor = db.execute(
        """
        SELECT t.*
        FROM tags t
        JOIN account_tags at ON t.id = at.tag_id
        WHERE at.account_id = ?
        ORDER BY t.created_at DESC
        """,
        (account_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def add_account_tag(account_id: int, tag_id: int) -> bool:
    """给账号添加标签"""
    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO account_tags (account_id, tag_id) VALUES (?, ?)",
            (account_id, tag_id),
        )
        db.commit()
        return True
    except Exception:
        return False


def remove_account_tag(account_id: int, tag_id: int) -> bool:
    """移除账号标签"""
    db = get_db()
    db.execute(
        "DELETE FROM account_tags WHERE account_id = ? AND tag_id = ?",
        (account_id, tag_id),
    )
    db.commit()
    return True
