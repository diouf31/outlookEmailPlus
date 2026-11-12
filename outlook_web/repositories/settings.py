from __future__ import annotations

import json
from typing import Any, Dict

from outlook_web import config
from outlook_web.db import create_sqlite_connection, get_db
from outlook_web.security.crypto import decrypt_data

DEFAULT_TEMP_MAIL_PROVIDER = "custom_domain_temp_mail"
LEGACY_TEMP_MAIL_PROVIDER = "legacy_bridge"
CLOUDFLARE_TEMP_MAIL_PROVIDER = "cloudflare_temp_mail"
LEGACY_TEMP_MAIL_PROVIDER_NAMES = {"legacy_bridge", "legacy_gptmail", "gptmail"}


def get_setting(key: str, default: str = "") -> str:
    """获取设置值"""
    db = None
    temp_conn = False
    try:
        db = get_db()
    except RuntimeError:
        db = create_sqlite_connection()
        temp_conn = True

    try:
        cursor = db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default
    finally:
        if temp_conn and db is not None:
            db.close()


def set_setting(key: str, value: str, *, commit: bool = True) -> bool:
    """设置值"""
    db = None
    temp_conn = False
    try:
        db = get_db()
    except RuntimeError:
        db = create_sqlite_connection()
        temp_conn = True

    try:
        db.execute(
            """
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (key, value),
        )
        if commit:
            db.commit()
        return True
    except Exception:
        return False
    finally:
        if temp_conn and db is not None:
            db.close()


def get_all_settings() -> Dict[str, str]:
    """获取所有设置"""
    db = None
    temp_conn = False
    try:
        db = get_db()
    except RuntimeError:
        db = create_sqlite_connection()
        temp_conn = True

    try:
        cursor = db.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        if temp_conn and db is not None:
            db.close()


def get_login_password() -> str:
    """获取登录密码（优先从数据库读取）"""
    password = get_setting("login_password")
    return password if password else config.get_login_password_default()


def get_legacy_gptmail_api_key() -> str:
    """兼容读取 legacy gptmail_api_key。"""
    return get_setting("gptmail_api_key")


def get_temp_mail_api_key() -> str:
    """获取正式临时邮箱 API Key，并兼容 legacy gptmail_api_key 回退。"""
    api_key = get_setting("temp_mail_api_key")
    if api_key:
        return api_key
    legacy_api_key = get_legacy_gptmail_api_key()
    if legacy_api_key:
        return legacy_api_key
    return config.get_temp_mail_api_key_default()


def get_gptmail_api_key() -> str:
    """legacy bridge 兼容入口。"""
    return get_temp_mail_api_key()


def normalize_temp_mail_provider_name(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return DEFAULT_TEMP_MAIL_PROVIDER
    if normalized.lower() in LEGACY_TEMP_MAIL_PROVIDER_NAMES:
        return LEGACY_TEMP_MAIL_PROVIDER
    return normalized


def get_supported_temp_mail_provider_names() -> set[str]:
    from outlook_web.temp_mail_registry import _REGISTRY

    return set(_REGISTRY.keys())


def is_supported_temp_mail_provider_name(value: str | None) -> bool:
    return normalize_temp_mail_provider_name(value) in get_supported_temp_mail_provider_names()


def validate_temp_mail_provider_name(value: str | None) -> str:
    normalized = normalize_temp_mail_provider_name(value)
    if normalized not in get_supported_temp_mail_provider_names():
        raise ValueError("临时邮箱 Provider 配置无效")
    return normalized


def get_temp_mail_provider() -> str:
    return normalize_temp_mail_provider_name(get_setting("temp_mail_provider", DEFAULT_TEMP_MAIL_PROVIDER))


def get_temp_mail_runtime_provider_name(provider_name: str | None = None) -> str:
    if provider_name is not None:
        return normalize_temp_mail_provider_name(provider_name)
    return get_temp_mail_provider()


def get_temp_mail_api_base_url() -> str:
    base_url = get_setting("temp_mail_api_base_url")
    return base_url if base_url else config.get_temp_mail_base_url()


def get_cf_worker_base_url() -> str:
    """获取 Cloudflare Temp Email Worker 独立部署地址（与 GPTMail 设置完全隔离）。"""
    return get_setting("cf_worker_base_url", "").strip()


def get_cf_worker_admin_key() -> str:
    """获取 Cloudflare Worker ADMIN_PASSWORDS 中的密码值（自动解密 enc: 格式）。"""
    value = get_setting("cf_worker_admin_key", "").strip()
    if not value:
        return ""
    try:
        return decrypt_data(value)
    except Exception:
        # 兼容历史明文值：解密失败时直接返回明文
        return value


def get_temp_mail_domains() -> list[dict[str, Any]]:
    raw = get_setting("temp_mail_domains", "[]")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def get_temp_mail_default_domain() -> str:
    return get_setting("temp_mail_default_domain", "").strip()


def get_temp_mail_prefix_rules() -> dict[str, Any]:
    raw = get_setting("temp_mail_prefix_rules", "{}")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def get_cf_worker_domains() -> list[dict[str, Any]]:
    """获取 CF Worker 独立域名列表（v0.3 Tab 重构）。"""
    raw = get_setting("cf_worker_domains", "[]")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def get_cf_worker_default_domain() -> str:
    """获取 CF Worker 默认域名（v0.3 Tab 重构）。"""
    return get_setting("cf_worker_default_domain", "").strip()


def get_cf_worker_prefix_rules() -> dict[str, Any]:
    """获取 CF Worker 前缀规则（v0.3 Tab 重构）。"""
    _default_rules: dict[str, Any] = {
        "min_length": 1,
        "max_length": 32,
        "pattern": "^[a-z0-9][a-z0-9._-]*$",
    }
    raw = get_setting("cf_worker_prefix_rules", "{}")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _default_rules
    if not isinstance(value, dict) or not value:
        return _default_rules
    return value


def get_external_api_key() -> str:
    """
    获取对外开放 API Key。

    - 若数据库为空，返回空字符串
    - 若为 enc: 加密格式，自动解密
    - 若为历史明文（兼容），直接返回明文
    - 解密失败时返回空字符串（避免影响外部接口鉴权逻辑）
    """
    value = get_setting("external_api_key") or ""
    if not value:
        return ""
    try:
        return decrypt_data(value)
    except Exception:
        return ""


def get_webhook_notification_enabled() -> bool:
    return get_setting("webhook_notification_enabled", "false").lower() == "true"


def get_webhook_notification_url() -> str:
    return get_setting("webhook_notification_url", "").strip()


def get_webhook_notification_token() -> str:
    """
    获取 Webhook Token。

    - 若数据库为空，返回空字符串
    - 若为 enc: 加密格式，自动解密
    - 解密失败时返回空字符串
    """
    value = get_setting("webhook_notification_token", "").strip()
    if not value:
        return ""
    try:
        return decrypt_data(value)
    except Exception:
        return ""


def get_webhook_notification_token_masked(head: int = 4, tail: int = 4) -> str:
    """Webhook Token 脱敏展示：前 N 位 + 若干 * + 后 N 位。"""
    token = get_webhook_notification_token()
    if not token:
        return ""
    safe_value = str(token)
    if len(safe_value) <= head + tail:
        return "*" * len(safe_value)
    return safe_value[:head] + ("*" * (len(safe_value) - head - tail)) + safe_value[-tail:]


def get_verification_ai_enabled() -> bool:
    return get_setting("verification_ai_enabled", "false").lower() == "true"


def get_verification_ai_base_url() -> str:
    return get_setting("verification_ai_base_url", "").strip()


def get_verification_ai_model() -> str:
    return get_setting("verification_ai_model", "").strip()


def get_verification_ai_api_key() -> str:
    """
    获取验证码 AI API Key。

    - 若为空，返回空字符串
    - 若为 enc: 加密格式，自动解密
    - 若为历史明文（兼容），直接返回明文
    """
    value = get_setting("verification_ai_api_key", "").strip()
    if not value:
        return ""
    try:
        return decrypt_data(value)
    except Exception:
        # 兼容历史明文
        return value


def get_external_api_key_masked(head: int = 4, tail: int = 4) -> str:
    """对外 API Key 脱敏展示：前 N 位 + 若干 * + 后 N 位。"""
    key = get_external_api_key()
    if not key:
        return ""
    safe_value = str(key)
    if len(safe_value) <= head + tail:
        return "*" * len(safe_value)
    return safe_value[:head] + ("*" * (len(safe_value) - head - tail)) + safe_value[-tail:]


# ── P1：公网模式安全配置 ──────────────────────────────


def get_external_api_public_mode() -> bool:
    """公网模式是否开启（默认关闭，保持 P0 受控私有行为）。"""
    return get_setting("external_api_public_mode", "false").lower() == "true"


def get_external_api_ip_whitelist() -> list:
    """IP 白名单列表（JSON 数组，支持 CIDR 如 '192.168.1.0/24'）。"""
    import json

    raw = get_setting("external_api_ip_whitelist", "[]")
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def get_external_api_rate_limit() -> int:
    """每分钟每 IP 最大请求数（默认 60）。"""
    try:
        val = int(get_setting("external_api_rate_limit_per_minute", "60"))
        return max(1, val)
    except (ValueError, TypeError):
        return 60


def get_external_api_disable_wait_message() -> bool:
    """是否禁用 wait-message 端点（默认不禁用）。"""
    return get_setting("external_api_disable_wait_message", "false").lower() == "true"


def get_external_api_disable_raw_content() -> bool:
    """是否禁用 raw 端点（默认不禁用）。"""
    return get_setting("external_api_disable_raw_content", "false").lower() == "true"


def get_pool_external_enabled() -> bool:
    return get_setting("pool_external_enabled", "false").lower() == "true"


def get_external_api_disable_pool_claim_random() -> bool:
    return get_setting("external_api_disable_pool_claim_random", "false").lower() == "true"


def get_external_api_disable_pool_claim_release() -> bool:
    return get_setting("external_api_disable_pool_claim_release", "false").lower() == "true"


def get_external_api_disable_pool_claim_complete() -> bool:
    return get_setting("external_api_disable_pool_claim_complete", "false").lower() == "true"


def get_external_api_disable_pool_stats() -> bool:
    return get_setting("external_api_disable_pool_stats", "false").lower() == "true"


USER_POLLING_KEYS = (
    "enable_auto_polling",
    "polling_interval",
    "polling_count",
)
_USER_POLLING_DEFAULTS = {
    "enable_auto_polling": "false",
    "polling_interval": "10",
    "polling_count": "5",
}


def get_user_setting(user_id: int, key: str, default: str = "") -> str:
    """读取指定用户的偏好设置。"""
    db = None
    temp_conn = False
    try:
        db = get_db()
    except RuntimeError:
        db = create_sqlite_connection()
        temp_conn = True
    try:
        row = db.execute(
            "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
            (int(user_id), key),
        ).fetchone()
        if row and row["value"] is not None:
            return str(row["value"])
        return default
    finally:
        if temp_conn and db is not None:
            db.close()


def set_user_setting(user_id: int, key: str, value: str, *, commit: bool = True) -> bool:
    """写入指定用户的偏好设置。"""
    db = None
    temp_conn = False
    try:
        db = get_db()
    except RuntimeError:
        db = create_sqlite_connection()
        temp_conn = True
    try:
        db.execute(
            """
            INSERT INTO user_settings (user_id, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(user_id), key, value),
        )
        if commit:
            db.commit()
        return True
    except Exception:
        return False
    finally:
        if temp_conn and db is not None:
            db.close()


def get_user_polling_settings(user_id: int | None) -> dict[str, str]:
    """读取当前用户的自动轮询偏好。

    - 有用户偏好：以 user_settings 为准
    - 无用户偏好：使用内置默认（关闭 / 10 秒 / 5 次），不再跟随全局 settings，
      避免一个用户改全局后影响其他用户
    """
    result = dict(_USER_POLLING_DEFAULTS)
    if not user_id:
        return result

    for key in USER_POLLING_KEYS:
        user_value = get_user_setting(user_id, key, "")
        if user_value != "":
            result[key] = user_value
    return result


def set_user_polling_setting(
    user_id: int,
    key: str,
    value: str,
    *,
    commit: bool = True,
) -> bool:
    if key not in USER_POLLING_KEYS:
        return False
    return set_user_setting(user_id, key, value, commit=commit)


def get_ui_layout_v2() -> dict:
    """读取前端布局状态"""
    import json

    raw = get_setting("ui_layout_v2", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def set_ui_layout_v2(layout: dict) -> None:
    """写入前端布局状态"""
    import json

    set_setting("ui_layout_v2", json.dumps(layout, ensure_ascii=False))


# ── Telegram 代理配置 ──────────────────────────────


def get_telegram_proxy_url() -> str:
    """获取 Telegram 推送使用的系统级代理 URL（明文存储，如 socks5://host:port）。"""
    return get_setting("telegram_proxy_url", "").strip()


def set_telegram_proxy_url(url: str) -> bool:
    """保存 Telegram 代理 URL。"""
    return set_setting("telegram_proxy_url", url.strip())


def get_telegram_bot_token() -> str:
    """获取 Telegram Bot Token（支持 enc: 加密格式）。"""
    from outlook_web.security.crypto import decrypt_data, is_encrypted

    value = get_setting("telegram_bot_token", "").strip()
    if not value:
        return ""
    if is_encrypted(value):
        try:
            return decrypt_data(value)
        except Exception:
            return ""
    return value


# ---- OAuth Token 工具配置 ----


def get_oauth_tool_client_id() -> str:
    """Settings 表 → 环境变量 → 空字符串"""
    value = get_setting("oauth_tool_client_id")
    if value:
        return value
    return config.get_oauth_client_id_default()


def get_oauth_tool_client_secret() -> str:
    """Settings 表（自动解密/兼容历史明文） → 环境变量 → 空字符串"""
    value = get_setting("oauth_tool_client_secret")
    if value:
        if not value.startswith("enc:"):
            return value
        try:
            return decrypt_data(value)
        except Exception:
            return ""
    return config.get_oauth_client_secret_default()


def get_oauth_tool_redirect_uri() -> str:
    """Settings 表 → 环境变量 → 空字符串"""
    value = get_setting("oauth_tool_redirect_uri")
    if value:
        return value
    return config.get_oauth_redirect_uri_default()


def get_oauth_tool_scope() -> str:
    """Settings 表 → 环境变量 → 默认 IMAP 兼容 scope"""
    value = get_setting("oauth_tool_scope")
    if value:
        return value
    return config.get_oauth_scope_default()


def get_oauth_tool_tenant() -> str:
    """Settings 表 → 环境变量 → 'consumers'"""
    value = get_setting("oauth_tool_tenant")
    if value:
        return value
    return config.get_oauth_tenant_default()


def get_oauth_tool_prompt_consent() -> bool:
    """Settings 表 → False"""
    value = get_setting("oauth_tool_prompt_consent", "false")
    return value.lower() == "true"
