from __future__ import annotations

from typing import Any

from flask import jsonify, request, session

from outlook_web import config
from outlook_web.repositories import users as users_repo
from outlook_web.security.auth import admin_required, get_current_user_id, login_required


# ==================== 用户管理 API ====================


@login_required
def get_me() -> Any:
    """获取当前登录用户信息"""
    user_id = get_current_user_id()
    user = users_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "用户不存在", "status": 404}}), 404
    return jsonify({"success": True, "user": user})


@login_required
def change_my_password() -> Any:
    """修改当前用户自己的密码"""
    if not config.get_allow_login_password_change():
        return jsonify({"success": False, "error": {"code": "FORBIDDEN", "message": "当前站点已禁用密码修改", "status": 403}}), 403
    data = request.json or {}
    new_password = (data.get("new_password") or "").strip()
    if len(new_password) < 8:
        return jsonify({"success": False, "error": {"code": "INVALID_PARAM", "message": "密码长度至少 8 位", "status": 400}}), 400
    user_id = get_current_user_id()
    ok = users_repo.update_user_password(user_id, new_password)
    if ok:
        return jsonify({"success": True, "message": "密码已修改"})
    return jsonify({"success": False, "error": {"code": "UPDATE_FAILED", "message": "修改失败", "status": 500}}), 500


@admin_required
def list_users() -> Any:
    """管理员：列出所有用户"""
    users = users_repo.list_users()
    return jsonify({"success": True, "users": users})


@admin_required
def create_user() -> Any:
    """管理员：创建新用户"""
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    role = (data.get("role") or "user").strip()
    if not username:
        return jsonify({"success": False, "error": {"code": "INVALID_PARAM", "message": "用户名不能为空", "status": 400}}), 400
    if len(password) < 8:
        return jsonify({"success": False, "error": {"code": "INVALID_PARAM", "message": "密码长度至少 8 位", "status": 400}}), 400
    if role not in ("admin", "user"):
        role = "user"
    new_id = users_repo.create_user(username, password, role)
    if new_id is None:
        return jsonify({"success": False, "error": {"code": "CREATE_FAILED", "message": "创建失败，用户名可能已存在", "status": 400}}), 400
    return jsonify({"success": True, "user_id": new_id, "message": "用户已创建"})


@admin_required
def delete_user(user_id: int) -> Any:
    """管理员：删除用户"""
    current_uid = get_current_user_id()
    if current_uid == user_id:
        return jsonify({"success": False, "error": {"code": "FORBIDDEN", "message": "不能删除自己", "status": 400}}), 400
    ok, err = users_repo.delete_user(user_id)
    if ok:
        return jsonify({"success": True, "message": "用户已删除"})
    return jsonify({"success": False, "error": {"code": "DELETE_FAILED", "message": err or "删除失败", "status": 400}}), 400


@admin_required
def reset_user_password(user_id: int) -> Any:
    """管理员：重置任意用户的密码"""
    data = request.json or {}
    new_password = (data.get("new_password") or "").strip()
    if len(new_password) < 8:
        return jsonify({"success": False, "error": {"code": "INVALID_PARAM", "message": "密码长度至少 8 位", "status": 400}}), 400
    ok = users_repo.update_user_password(user_id, new_password)
    if ok:
        return jsonify({"success": True, "message": "密码已重置"})
    return jsonify({"success": False, "error": {"code": "UPDATE_FAILED", "message": "重置失败", "status": 500}}), 500


@admin_required
def update_user_role(user_id: int) -> Any:
    """管理员：修改用户角色"""
    data = request.json or {}
    role = (data.get("role") or "").strip()
    if role not in ("admin", "user"):
        return jsonify({"success": False, "error": {"code": "INVALID_PARAM", "message": "角色无效，只支持 admin/user", "status": 400}}), 400
    ok = users_repo.update_user_role(user_id, role)
    if ok:
        return jsonify({"success": True, "message": "角色已更新"})
    return jsonify({"success": False, "error": {"code": "UPDATE_FAILED", "message": "更新失败（可能是不能撤销最后一个管理员）", "status": 400}}), 400
