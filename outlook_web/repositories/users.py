from __future__ import annotations

from typing import Any, Dict, List, Optional

from outlook_web.db import get_db
from outlook_web.security.crypto import hash_password, verify_password


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """根据 ID 获取用户（不含密码哈希）"""
    db = get_db()
    row = db.execute(
        "SELECT id, username, role, created_at, updated_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """根据用户名获取用户（含密码哈希，供登录验证使用）"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    """列出所有用户（不含密码哈希）"""
    db = get_db()
    rows = db.execute(
        "SELECT id, username, role, created_at, updated_at FROM users ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def create_user(username: str, password: str, role: str = "user") -> Optional[int]:
    """创建用户，同时为其初始化默认分组。失败返回 None。"""
    if not username or not password:
        return None
    if role not in ("admin", "user"):
        role = "user"
    db = get_db()
    try:
        pw_hash = hash_password(password)
        cursor = db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username.strip(), pw_hash, role),
        )
        db.commit()
        new_id = int(cursor.lastrowid)
        _init_user_groups(new_id)
        return new_id
    except Exception:
        return None


def _init_user_groups(user_id: int) -> None:
    """为新用户初始化默认分组（默认分组 + 临时邮箱分组）。"""
    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO groups (name, description, color, user_id) VALUES (?, ?, ?, ?)",
            ("默认分组", "未分组的邮箱", "#666666", user_id),
        )
        db.execute(
            "INSERT OR IGNORE INTO groups (name, description, color, is_system, user_id) VALUES (?, ?, ?, ?, ?)",
            ("临时邮箱", "自建临时邮箱服务", "#00bcf2", 1, user_id),
        )
        db.commit()
    except Exception:
        pass


def update_user_password(user_id: int, new_password: str) -> bool:
    """修改用户密码。"""
    if not new_password:
        return False
    db = get_db()
    try:
        pw_hash = hash_password(new_password)
        cursor = db.execute(
            "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pw_hash, user_id),
        )
        db.commit()
        return cursor.rowcount > 0
    except Exception:
        return False


def update_user_role(user_id: int, role: str) -> bool:
    """修改用户角色（admin/user）。不允许撤销最后一个 admin。"""
    if role not in ("admin", "user"):
        return False
    db = get_db()
    if role == "user":
        admin_count = db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ).fetchone()[0]
        current_user_row = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if current_user_row and current_user_row[0] == "admin" and int(admin_count) <= 1:
            return False
    try:
        cursor = db.execute(
            "UPDATE users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (role, user_id),
        )
        db.commit()
        return cursor.rowcount > 0
    except Exception:
        return False


def delete_user(user_id: int) -> tuple[bool, str]:
    """
    删除用户及其所有数据（accounts、groups、tags）。
    不允许删除最后一个 admin。
    返回 (success, error_msg)。
    """
    db = get_db()
    try:
        user_row = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user_row:
            return False, "用户不存在"
        if user_row[0] == "admin":
            admin_count = int(db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0])
            if admin_count <= 1:
                return False, "不能删除最后一个管理员"

        db.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM tags WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM groups WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        return True, ""
    except Exception as e:
        return False, str(e)


def validate_login(username: str, password: str) -> Optional[Dict[str, Any]]:
    """验证登录，成功返回用户信息字典（含 id/username/role），失败返回 None。"""
    user = get_user_by_username(username)
    if not user:
        return None
    if verify_password(password, user.get("password_hash", "")):
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
        }
    return None
