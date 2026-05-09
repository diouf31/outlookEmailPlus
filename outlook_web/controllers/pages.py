from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import g, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from outlook_web.errors import build_error_payload
from outlook_web.repositories import users as users_repo
from outlook_web.security.auth import (
    check_rate_limit,
    get_client_ip,
    login_required,
    record_login_failure,
    reset_login_attempts,
)

# ==================== 页面路由 ====================


def login() -> Any:
    """登录页面"""
    if request.method == "POST":
        try:
            client_ip = get_client_ip()

            allowed, remaining_time = check_rate_limit(client_ip)
            if not allowed:
                trace_id_value = None
                try:
                    trace_id_value = getattr(g, "trace_id", None)
                except Exception:
                    trace_id_value = None
                error_payload = build_error_payload(
                    code="LOGIN_RATE_LIMITED",
                    message=f"登录失败次数过多，请在 {remaining_time} 秒后重试",
                    err_type="RateLimitError",
                    status=429,
                    details=f"ip={client_ip}",
                    trace_id=trace_id_value,
                )
                return jsonify({"success": False, "error": error_payload}), 429

            data = request.json if request.is_json else request.form
            username = (data.get("username") or "").strip()
            password = data.get("password", "")

            if not username:
                return jsonify({"success": False, "error": {"code": "LOGIN_INVALID_CREDENTIALS", "message": "请输入用户名", "status": 401}}), 401

            user = users_repo.validate_login(username, password)

            if user:
                reset_login_attempts(client_ip)
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["user_role"] = user["role"]
                session.permanent = True
                session.modified = True
                return jsonify({"success": True, "message": "登录成功"})
            else:
                record_login_failure(client_ip)
                trace_id_value = None
                try:
                    trace_id_value = getattr(g, "trace_id", None)
                except Exception:
                    trace_id_value = None
                error_payload = build_error_payload(
                    code="LOGIN_INVALID_CREDENTIALS",
                    message="用户名或密码错误",
                    message_en="Invalid username or password",
                    err_type="AuthError",
                    status=401,
                    details=f"ip={client_ip}",
                    trace_id=trace_id_value,
                )
                return jsonify({"success": False, "error": error_payload}), 401
        except Exception as e:
            trace_id_value = None
            try:
                trace_id_value = getattr(g, "trace_id", None)
            except Exception:
                trace_id_value = None
            from flask import current_app

            try:
                current_app.logger.exception("Login error trace_id=%s", trace_id_value or "unknown")
            except Exception:
                pass
            error_payload = build_error_payload(
                code="LOGIN_FAILED",
                message="登录处理失败",
                err_type="AuthError",
                status=500,
                details=str(e),
                trace_id=trace_id_value,
            )
            return jsonify({"success": False, "error": error_payload}), 500

    return render_template("login.html")


def logout() -> Any:
    """退出登录"""
    session.pop("user_id", None)
    session.pop("username", None)
    session.pop("user_role", None)
    return redirect(url_for("pages.login"))


def image_asset(filename: str) -> Any:
    """提供仓库 img/ 目录中的静态图片资源。"""
    img_dir = Path(__file__).resolve().parents[2] / "img"
    return send_from_directory(str(img_dir), filename)


def favicon() -> Any:
    """站点 favicon，复用 img/ico.png。"""
    img_dir = Path(__file__).resolve().parents[2] / "img"
    return send_from_directory(str(img_dir), "ico.png", mimetype="image/png")


@login_required
def index() -> Any:
    """主页"""
    return render_template("index.html")


def get_csrf_token() -> Any:
    """获取CSRF Token"""
    from outlook_web.security.csrf import CSRF_AVAILABLE, generate_csrf

    if CSRF_AVAILABLE:
        token = generate_csrf()
        return jsonify({"csrf_token": token})
    else:
        return jsonify({"csrf_token": None, "csrf_disabled": True})
