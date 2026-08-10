from __future__ import annotations

import math
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

from outlook_web.db import get_db
from outlook_web.security.auth import get_current_user_id, get_current_user_role, get_current_username


def _db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    return conn or get_db()


def _account_user_filter_sql(alias: str = "") -> tuple[str, list[Any]]:
    """与账号管理一致：有登录用户时只统计该用户的账号。"""
    user_id = get_current_user_id()
    prefix = f"{alias}." if alias else ""
    if user_id:
        return f" AND {prefix}user_id = ? ", [user_id]
    return "", []


def _verification_user_join_sql() -> tuple[str, str, list[Any]]:
    """
    验证码日志按账号归属过滤。
    返回 (join_sql, where_sql, params)。无登录用户时不限制（兼容后台/测试）。
    有用户时仅统计其 accounts 上的日志，排除共享临时邮箱日志。
    """
    user_id = get_current_user_id()
    if not user_id:
        return "", "", []
    return (
        " INNER JOIN accounts AS _ua ON vel.account_id > 0 AND _ua.id = vel.account_id ",
        " AND _ua.user_id = ? ",
        [user_id],
    )


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def _percentile95(values: Iterable[int]) -> int:
    numbers = sorted(int(value or 0) for value in values)
    if not numbers:
        return 0
    index = max(0, math.ceil(len(numbers) * 0.95) - 1)
    return numbers[index]


def _channel_label(channel: str) -> str:
    return {
        "graph_inbox": "Graph Inbox",
        "graph_junk": "Graph Junk",
        "imap_new": "IMAP New",
        "imap_old": "IMAP Old",
        "temp_mail": "Temp Mail",
        "ai_fallback": "AI Fallback",
        "graph_delta": "Graph",
        "imap_ssl": "IMAP",
    }.get(str(channel or "").strip(), str(channel or "") or "unknown")


def _action_group(action: str) -> str:
    text = str(action or "").strip().lower()
    if text.startswith("external_") or "external" in text:
        return "external_api"
    if text in {"claim", "complete", "release", "expire", "read"} or "pool" in text:
        return "pool_op"
    if "setting" in text or "config" in text:
        return "settings"
    if "account" in text or "group" in text or "tag" in text:
        return "account_change"
    return "other"


def get_overview_summary(conn: sqlite3.Connection | None = None) -> Dict[str, Any]:
    # 概览大盘入口：聚合账号状态、邮箱池快照、刷新健康度、今日 KPI，供前端 dashboard 一次性加载
    db = _db(conn)
    user_sql, user_params = _account_user_filter_sql()

    account_rows = db.execute(
        f"""
        SELECT COALESCE(status, '') AS status, COUNT(*) AS cnt
        FROM accounts
        WHERE 1=1{user_sql}
        GROUP BY COALESCE(status, '')
        """,
        tuple(user_params),
    ).fetchall()
    account_status = {
        "total": 0,
        "active": 0,
        "expired": 0,
        "pending_refresh": 0,
        "error": 0,
    }
    for row in account_rows:
        status = str(row["status"] or "").strip().lower()
        count = int(row["cnt"] or 0)
        account_status["total"] += count
        if status == "active":
            account_status["active"] += count
        elif status in {"expired", "inactive", "disabled"}:
            account_status["expired"] += count
        elif status in {"pending_refresh", "refresh_required"}:
            account_status["pending_refresh"] += count
        elif status in {"error", "failed"}:
            account_status["error"] += count

    pool_rows = db.execute(
        f"""
        SELECT COALESCE(pool_status, '') AS pool_status, COUNT(*) AS cnt
        FROM accounts
        WHERE pool_status IS NOT NULL{user_sql}
        GROUP BY COALESCE(pool_status, '')
        """,
        tuple(user_params),
    ).fetchall()
    pool_snapshot = {
        "available": 0,
        "in_use": 0,
        "cooldown": 0,
        "used": 0,
        "disabled": 0,
        "total": 0,
        "usage_rate": 0.0,
    }
    for row in pool_rows:
        status = str(row["pool_status"] or "").strip().lower()
        count = int(row["cnt"] or 0)
        pool_snapshot["total"] += count
        if status == "available":
            pool_snapshot["available"] += count
        elif status == "claimed":
            pool_snapshot["in_use"] += count
        elif status == "cooldown":
            pool_snapshot["cooldown"] += count
        elif status == "used":
            pool_snapshot["used"] += count
        elif status in {"frozen", "retired"}:
            pool_snapshot["disabled"] += count
    pool_snapshot["usage_rate"] = _safe_div(pool_snapshot["in_use"], pool_snapshot["total"])

    # 刷新健康度：有用户时按该用户账号的 refresh logs 统计；否则沿用全局 refresh_runs
    duration_seconds = 0
    if user_params:
        refresh_last = db.execute(
            """
            SELECT MAX(arl.created_at) AS started_at,
                   SUM(CASE WHEN lower(COALESCE(arl.status,'')) IN ('success','ok') THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN lower(COALESCE(arl.status,'')) NOT IN ('success','ok') THEN 1 ELSE 0 END) AS failed_count
            FROM account_refresh_logs AS arl
            INNER JOIN accounts AS a ON a.id = arl.account_id
            WHERE a.user_id = ?
              AND datetime(arl.created_at) >= datetime('now', '-1 day')
            """,
            tuple(user_params),
        ).fetchone()
        refresh_7d = db.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN lower(COALESCE(arl.status,'')) IN ('success','ok') THEN 1 ELSE 0 END), 0) AS success_sum,
                   COALESCE(COUNT(*), 0) AS total_sum
            FROM account_refresh_logs AS arl
            INNER JOIN accounts AS a ON a.id = arl.account_id
            WHERE a.user_id = ?
              AND datetime(arl.created_at) >= datetime('now', '-7 day')
            """,
            tuple(user_params),
        ).fetchone()
    else:
        refresh_last = db.execute("""
            SELECT started_at, finished_at, total, success_count, failed_count
            FROM refresh_runs
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """).fetchone()
        refresh_7d = db.execute("""
            SELECT COALESCE(SUM(success_count), 0) AS success_sum,
                   COALESCE(SUM(total), 0) AS total_sum
            FROM refresh_runs
            WHERE datetime(started_at) >= datetime('now', '-7 day')
            """).fetchone()
        if refresh_last and refresh_last["started_at"] and refresh_last["finished_at"]:
            duration_row = db.execute(
                """
                SELECT CAST((julianday(?) - julianday(?)) * 86400 AS INTEGER) AS duration_s
                """,
                (refresh_last["finished_at"], refresh_last["started_at"]),
            ).fetchone()
            duration_seconds = int(duration_row["duration_s"] or 0) if duration_row else 0

    today_start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    v_join, v_where, v_params = _verification_user_join_sql()
    today_logs = db.execute(
        f"""
        SELECT COUNT(*) AS verification_count
        FROM verification_extract_logs AS vel
        {v_join}
        WHERE vel.started_at >= ?{v_where}
        """,
        tuple([today_start] + v_params),
    ).fetchone()

    # 临时邮箱表无 user_id：多用户隔离下不展示共享池数据，避免串号
    if user_params:
        emails_received = 0
        temp_emails_active = 0
    else:
        today_messages = db.execute("""
            SELECT COUNT(*) AS message_count
            FROM temp_email_messages
            WHERE datetime(created_at) >= datetime('now', 'start of day')
            """).fetchone()
        temp_mail_active = db.execute("""
            SELECT COUNT(*) AS active_count
            FROM temp_emails
            WHERE COALESCE(status, 'active') = 'active'
            """).fetchone()
        emails_received = int(today_messages["message_count"] or 0) if today_messages else 0
        temp_emails_active = int(temp_mail_active["active_count"] or 0) if temp_mail_active else 0

    return {
        "account_status": account_status,
        "pool_snapshot": pool_snapshot,
        "refresh_health": {
            "last_run_at": refresh_last["started_at"] if refresh_last else None,
            "last_success_count": int(refresh_last["success_count"] or 0) if refresh_last else 0,
            "last_fail_count": int(refresh_last["failed_count"] or 0) if refresh_last else 0,
            "last_duration_s": duration_seconds,
            "success_rate_7d": _safe_div(
                int(refresh_7d["success_sum"] or 0) if refresh_7d else 0,
                int(refresh_7d["total_sum"] or 0) if refresh_7d else 0,
            ),
        },
        "kpi": {
            "emails_received": emails_received,
            "verification_extracted": int(today_logs["verification_count"] or 0) if today_logs else 0,
            "temp_emails_active": temp_emails_active,
        },
    }


def get_verification_stats(
    conn: sqlite3.Connection | None = None,
    *,
    days: int = 7,
    recent_limit: int = 10,
) -> Dict[str, Any]:
    # 验证码提取统计：汇总成功率、渠道分布、AI 增强效果、P95 延迟，支持运营洞察
    db = _db(conn)
    cutoff = time.time() - max(int(days), 1) * 86400
    v_join, v_where, v_params = _verification_user_join_sql()

    rows = db.execute(
        f"""
        SELECT vel.id, vel.account_id, vel.channel, vel.started_at, vel.finished_at, vel.duration_ms,
               vel.result_type, vel.code_found, vel.used_ai, vel.error_code, vel.trace_id
        FROM verification_extract_logs AS vel
        {v_join}
        WHERE vel.started_at >= ?{v_where}
        ORDER BY vel.started_at DESC, vel.id DESC
        """,
        tuple([cutoff] + v_params),
    ).fetchall()

    total_count = len(rows)
    success_count = sum(1 for row in rows if str(row["result_type"] or "") != "none")
    fail_count = total_count - success_count
    ai_rows = [row for row in rows if int(row["used_ai"] or 0) == 1]
    ai_success = sum(1 for row in ai_rows if str(row["result_type"] or "") != "none")
    durations = [int(row["duration_ms"] or 0) for row in rows]

    channel_stats_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        channel = str(row["channel"] or "") or "unknown"
        entry = channel_stats_map.setdefault(
            channel,
            {
                "channel": channel,
                "label": _channel_label(channel),
                "count": 0,
                "success_count": 0,
                "duration_total_ms": 0,
            },
        )
        entry["count"] += 1
        entry["duration_total_ms"] += int(row["duration_ms"] or 0)
        if str(row["result_type"] or "") != "none":
            entry["success_count"] += 1

    channel_stats = []
    for entry in channel_stats_map.values():
        count = int(entry["count"] or 0)
        success = int(entry["success_count"] or 0)
        duration_total = int(entry["duration_total_ms"] or 0)
        channel_stats.append(
            {
                "channel": entry["channel"],
                "label": entry["label"],
                "count": count,
                "success_count": success,
                "success_rate": _safe_div(success, count),
                "avg_duration_ms": int(duration_total / count) if count else 0,
            }
        )
    channel_stats.sort(key=lambda item: (-int(item["count"]), item["channel"]))

    # account_id > 0 对应 accounts 表，< 0 对应 temp_emails 表（取反编码，见 encode_temp_mail_log_account_id）
    # 多用户隔离时只展示当前用户账号相关记录（不含共享临时邮箱）
    if v_params:
        recent_rows = db.execute(
            f"""
            SELECT vel.id, vel.started_at, vel.channel, vel.code_found, vel.duration_ms,
                   vel.result_type, vel.used_ai, vel.error_code,
                   COALESCE(a.email, '') AS account_email
            FROM verification_extract_logs AS vel
            INNER JOIN accounts AS a ON vel.account_id > 0 AND a.id = vel.account_id
            WHERE a.user_id = ?
            ORDER BY vel.started_at DESC, vel.id DESC
            LIMIT ?
            """,
            (v_params[0], max(int(recent_limit), 1)),
        ).fetchall()
    else:
        recent_rows = db.execute(
            """
            SELECT vel.id, vel.started_at, vel.channel, vel.code_found, vel.duration_ms,
                   vel.result_type, vel.used_ai, vel.error_code,
                   COALESCE(a.email, tm.email, '') AS account_email
            FROM verification_extract_logs AS vel
            LEFT JOIN accounts AS a ON vel.account_id > 0 AND a.id = vel.account_id
            LEFT JOIN temp_emails AS tm ON vel.account_id < 0 AND tm.id = -vel.account_id
            ORDER BY vel.started_at DESC, vel.id DESC
            LIMIT ?
            """,
            (max(int(recent_limit), 1),),
        ).fetchall()
    recent = [
        {
            "id": int(row["id"]),
            "started_at": float(row["started_at"] or 0),
            "account_email": row["account_email"] or "",
            "channel": row["channel"] or "",
            "channel_label": _channel_label(str(row["channel"] or "")),
            "code_found": row["code_found"],
            "duration_ms": int(row["duration_ms"] or 0),
            "result_type": row["result_type"] or "none",
            "used_ai": bool(row["used_ai"]),
            "error_code": row["error_code"],
        }
        for row in recent_rows
    ]

    return {
        "kpi": {
            "total_count": total_count,
            "success_count": success_count,
            "fail_count": fail_count,
            "daily_avg": int(total_count / max(int(days), 1)) if total_count else 0,
            "success_rate": _safe_div(success_count, total_count),
            "ai_used_count": len(ai_rows),
            "ai_success_rate": _safe_div(ai_success, len(ai_rows)),
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
            "p95_duration_ms": _percentile95(durations),
        },
        "channel_stats": channel_stats,
        "recent": recent,
    }


def get_external_api_stats(conn: sqlite3.Connection | None = None, *, days: int = 7) -> Dict[str, Any]:
    # 外部 API 消费者统计：按调用方维度聚合调用量、成功率、端点分布，用于 API 用量监控
    # 表无 user_id：普通 user 角色不展示系统级 API 用量；admin 可见（实例级共享配置）
    empty = {
        "kpi": {
            "today_calls": 0,
            "week_calls": 0,
            "today_vs_yesterday_rate": 0.0,
            "success_rate": 0.0,
            "error_count": 0,
            "active_callers": 0,
        },
        "daily_series": [],
        "by_endpoint": [],
        "caller_rank": [],
    }
    if get_current_user_id() and get_current_user_role() != "admin":
        days = max(int(days), 1)
        today = date.today()
        empty["daily_series"] = [
            {"date": (today - timedelta(days=offset)).isoformat(), "count": 0}
            for offset in range(days - 1, -1, -1)
        ]
        return empty

    db = _db(conn)
    days = max(int(days), 1)
    today = date.today()
    day_list = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    day_set = set(day_list)

    rows = db.execute("""
        SELECT consumer_key,
               consumer_name,
               caller_id,
               COALESCE(NULLIF(usage_date, ''), NULLIF(date, ''), '') AS usage_day,
               endpoint,
               total_count,
               call_count,
               success_count,
               error_count,
               last_used_at
        FROM external_api_consumer_usage_daily
        """).fetchall()

    filtered = []
    for row in rows:
        usage_day = str(row["usage_day"] or "")
        if usage_day in day_set:
            total_value = int(row["call_count"] or 0) if int(row["call_count"] or 0) > 0 else int(row["total_count"] or 0)
            filtered.append(
                {
                    "consumer_key": row["consumer_key"] or "",
                    "consumer_name": row["consumer_name"] or "",
                    "caller_id": row["caller_id"] or "",
                    "usage_day": usage_day,
                    "endpoint": row["endpoint"] or "",
                    "total_count": total_value,
                    "success_count": int(row["success_count"] or 0),
                    "error_count": int(row["error_count"] or 0),
                    "last_used_at": row["last_used_at"] or "",
                }
            )

    daily_map = {day: 0 for day in day_list}
    endpoint_map: Dict[str, int] = {}
    caller_map: Dict[str, Dict[str, Any]] = {}
    week_calls = 0
    week_success = 0
    week_errors = 0
    today_calls = 0

    for row in filtered:
        count = int(row["total_count"] or 0)
        success_count = int(row["success_count"] or 0)
        error_count = int(row["error_count"] or 0)
        day = row["usage_day"]
        daily_map[day] = daily_map.get(day, 0) + count
        endpoint = row["endpoint"] or ""
        endpoint_map[endpoint] = endpoint_map.get(endpoint, 0) + count

        caller_key = row["consumer_key"] or row["caller_id"] or row["consumer_name"] or "unknown"
        caller = caller_map.setdefault(
            caller_key,
            {
                "caller_id": row["caller_id"] or caller_key,
                "consumer_key": row["consumer_key"] or caller_key,
                "display_name": row["consumer_name"] or row["caller_id"] or caller_key,
                "today_calls": 0,
                "week_calls": 0,
                "success_count": 0,
                "total_count": 0,
                "last_used_at": "",
            },
        )
        caller["week_calls"] += count
        caller["success_count"] += success_count
        caller["total_count"] += count
        if day == today.isoformat():
            caller["today_calls"] += count
            today_calls += count
        if row["last_used_at"] and str(row["last_used_at"]) > str(caller["last_used_at"]):
            caller["last_used_at"] = row["last_used_at"]

        week_calls += count
        week_success += success_count
        week_errors += error_count

    yesterday_calls = daily_map.get((today - timedelta(days=1)).isoformat(), 0)
    daily_series = [{"date": day, "count": daily_map.get(day, 0)} for day in day_list]
    caller_rank = [
        {
            "caller_id": item["caller_id"],
            "consumer_key": item["consumer_key"],
            "key_name": item["display_name"],
            "today_calls": item["today_calls"],
            "week_calls": item["week_calls"],
            "success_rate": _safe_div(item["success_count"], item["total_count"]),
            "last_used_at": item["last_used_at"],
        }
        for item in sorted(caller_map.values(), key=lambda value: (-value["week_calls"], value["display_name"]))
    ]

    total_endpoint_calls = sum(endpoint_map.values())
    by_endpoint = [
        {
            "endpoint": endpoint,
            "count": count,
            "rate": _safe_div(count, total_endpoint_calls),
        }
        for endpoint, count in sorted(endpoint_map.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "kpi": {
            "today_calls": today_calls,
            "week_calls": week_calls,
            "today_vs_yesterday_rate": _safe_div(today_calls - yesterday_calls, yesterday_calls) if yesterday_calls else 0.0,
            "success_rate": _safe_div(week_success, week_calls),
            "error_count": week_errors,
            "active_callers": sum(1 for item in caller_map.values() if item["week_calls"] > 0),
        },
        "daily_series": daily_series,
        "by_endpoint": by_endpoint,
        "caller_rank": caller_rank,
    }


def get_pool_stats(conn: sqlite3.Connection | None = None, *, days: int = 7) -> Dict[str, Any]:
    # 邮箱池统计：汇总可用/占用/冷却分布、操作统计、项目 Top5 使用率，帮助运营判断资源充足度
    db = _db(conn)
    cutoff_dt = (datetime.now(timezone.utc) - timedelta(days=max(int(days), 1))).strftime("%Y-%m-%dT%H:%M:%SZ")
    user_sql, user_params = _account_user_filter_sql()
    user_sql_a, _ = _account_user_filter_sql("a")

    pool_counts = {
        "available": 0,
        "in_use": 0,
        "cooldown": 0,
        "used": 0,
    }
    rows = db.execute(
        f"""
        SELECT COALESCE(pool_status, '') AS pool_status, COUNT(*) AS cnt
        FROM accounts
        WHERE pool_status IS NOT NULL{user_sql}
        GROUP BY COALESCE(pool_status, '')
        """,
        tuple(user_params),
    ).fetchall()
    for row in rows:
        status = str(row["pool_status"] or "").strip().lower()
        count = int(row["cnt"] or 0)
        if status == "available":
            pool_counts["available"] += count
        elif status == "claimed":
            pool_counts["in_use"] += count
        elif status == "cooldown":
            pool_counts["cooldown"] += count
        elif status == "used":
            pool_counts["used"] += count

    dist_rows = db.execute(
        f"""
        SELECT l.action, l.result, COUNT(*) AS cnt
        FROM account_claim_logs AS l
        INNER JOIN accounts AS a ON a.id = l.account_id
        WHERE COALESCE(l.claimed_at, l.created_at, '') >= ?{user_sql_a}
        GROUP BY l.action, l.result
        """,
        tuple([cutoff_dt] + user_params),
    ).fetchall()
    operation_distribution = {
        "claim": 0,
        "complete": 0,
        "complete_success": 0,
        "complete_fail": 0,
        "release": 0,
        "expire": 0,
    }
    for row in dist_rows:
        action = str(row["action"] or "").strip().lower()
        result = str(row["result"] or "").strip().lower()
        count = int(row["cnt"] or 0)
        if action == "claim":
            operation_distribution["claim"] += count
        elif action == "complete":
            operation_distribution["complete"] += count
            if result == "success":
                operation_distribution["complete_success"] += count
            else:
                operation_distribution["complete_fail"] += count
        elif action == "release":
            operation_distribution["release"] += count
        elif action == "expire":
            operation_distribution["expire"] += count

    max_claimed = db.execute(
        f"""
        SELECT MAX(CAST((julianday('now') - julianday(claimed_at)) * 86400 AS INTEGER)) AS max_claim_s
        FROM accounts
        WHERE pool_status = 'claimed' AND claimed_at IS NOT NULL{user_sql}
        """,
        tuple(user_params),
    ).fetchone()
    claim_count = operation_distribution["claim"]
    complete_count = operation_distribution["complete"]
    complete_success = operation_distribution["complete_success"]

    top_project_rows = db.execute(
        f"""
        SELECT u.project_key,
               COUNT(DISTINCT u.account_id) AS account_count,
               COALESCE(SUM(u.success_count), 0) AS success_count,
               COUNT(*) AS row_count
        FROM account_project_usage AS u
        INNER JOIN accounts AS a ON a.id = u.account_id
        WHERE COALESCE(u.project_key, '') != ''{user_sql_a}
        GROUP BY u.project_key
        ORDER BY success_count DESC, account_count DESC, u.project_key ASC
        LIMIT 5
        """,
        tuple(user_params),
    ).fetchall()
    project_top5 = [
        {
            "project_key": row["project_key"] or "",
            "account_count": int(row["account_count"] or 0),
            "success_count": int(row["success_count"] or 0),
            "reuse_rate": _safe_div(int(row["success_count"] or 0), int(row["row_count"] or 0)),
        }
        for row in top_project_rows
    ]

    recent_rows = db.execute(
        f"""
        SELECT COALESCE(l.claimed_at, l.created_at) AS action_time,
               a.email AS account_email,
               l.action,
               l.caller_id,
               a.claimed_project_key AS project_key,
               l.result
        FROM account_claim_logs AS l
        INNER JOIN accounts AS a ON a.id = l.account_id
        WHERE 1=1{user_sql_a}
        ORDER BY action_time DESC, l.id DESC
        LIMIT 10
        """,
        tuple(user_params),
    ).fetchall()
    recent_operations = [
        {
            "time": row["action_time"] or "",
            "account_email": row["account_email"] or "",
            "action": row["action"] or "",
            "caller_id": row["caller_id"] or "",
            "project_key": row["project_key"] or "",
            "result": row["result"] or "",
        }
        for row in recent_rows
    ]

    return {
        "kpi": {
            "available": pool_counts["available"],
            "in_use": pool_counts["in_use"],
            "cooldown": pool_counts["cooldown"],
            "used": pool_counts["used"],
            "max_claimed_duration_s": int(max_claimed["max_claim_s"] or 0) if max_claimed else 0,
            "claim_count_7d": claim_count,
            "complete_success_rate": _safe_div(complete_success, complete_count),
        },
        "operation_distribution": operation_distribution,
        "project_top5": project_top5,
        "recent_operations": recent_operations,
    }


def get_activity_stats(
    conn: sqlite3.Connection | None = None,
    *,
    hours: int = 24,
    timeline_limit: int = 20,
) -> Dict[str, Any]:
    # 活动时间线：合并审计日志 + 通知推送 + 验证码提取三种事件源，按时间倒序统一展示
    db = _db(conn)
    hours = max(int(hours), 1)
    username = get_current_username()
    user_id = get_current_user_id()

    if username:
        audit_rows = db.execute(
            """
            SELECT action, resource_type, operator, status, created_at
            FROM audit_logs
            WHERE datetime(created_at) >= datetime('now', ?)
              AND operator = ?
            ORDER BY created_at DESC, id DESC
            """,
            (f"-{hours} hour", username),
        ).fetchall()
    else:
        audit_rows = db.execute(
            """
            SELECT action, resource_type, operator, status, created_at
            FROM audit_logs
            WHERE datetime(created_at) >= datetime('now', ?)
            ORDER BY created_at DESC, id DESC
            """,
            (f"-{hours} hour",),
        ).fetchall()

    if user_id:
        notification_rows = db.execute(
            """
            SELECT n.channel, n.status, n.created_at, n.delivered_at
            FROM notification_delivery_logs AS n
            WHERE datetime(n.created_at) >= datetime('now', ?)
              AND (
                    n.source_key IN (SELECT email FROM accounts WHERE user_id = ?)
                    OR n.source_key IN (
                        SELECT CAST(id AS TEXT) FROM accounts WHERE user_id = ?
                    )
                  )
            ORDER BY n.created_at DESC, n.id DESC
            """,
            (f"-{hours} hour", user_id, user_id),
        ).fetchall()
    else:
        notification_rows = db.execute(
            """
            SELECT channel, status, created_at, delivered_at
            FROM notification_delivery_logs
            WHERE datetime(created_at) >= datetime('now', ?)
            ORDER BY created_at DESC, id DESC
            """,
            (f"-{hours} hour",),
        ).fetchall()

    verification_cutoff = time.time() - hours * 3600
    v_join, v_where, v_params = _verification_user_join_sql()
    verification_rows = db.execute(
        f"""
        SELECT vel.started_at, vel.channel, vel.result_type, vel.code_found, vel.duration_ms
        FROM verification_extract_logs AS vel
        {v_join}
        WHERE vel.started_at >= ?{v_where}
        ORDER BY vel.started_at DESC, vel.id DESC
        """,
        tuple([verification_cutoff] + v_params),
    ).fetchall()

    op_type_map: Dict[str, int] = {}
    for row in audit_rows:
        key = _action_group(str(row["action"] or ""))
        op_type_map[key] = op_type_map.get(key, 0) + 1

    notification_stats: Dict[str, Dict[str, Any]] = {}
    for row in notification_rows:
        channel = str(row["channel"] or "").strip() or "unknown"
        item = notification_stats.setdefault(channel, {"count": 0, "success_count": 0, "success_rate": 0.0})
        item["count"] += 1
        if str(row["status"] or "").strip().lower() == "sent":
            item["success_count"] += 1
    for item in notification_stats.values():
        item["success_rate"] = _safe_div(item["success_count"], item["count"])

    timeline: List[Dict[str, Any]] = []
    for row in audit_rows:
        timeline.append(
            {
                "time": row["created_at"] or "",
                "action": row["action"] or "",
                "status": row["status"] or "ok",
                "resource_type": row["resource_type"] or "",
                "operator": row["operator"] or "",
            }
        )
    for row in notification_rows:
        timeline.append(
            {
                "time": row["delivered_at"] or row["created_at"] or "",
                "action": f"notification:{row['channel'] or 'unknown'}",
                "status": row["status"] or "",
            }
        )
    for row in verification_rows:
        timeline.append(
            {
                "time": datetime.fromtimestamp(float(row["started_at"] or 0), tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "action": "verification_extract",
                "status": "success" if str(row["result_type"] or "") != "none" else "failed",
                "channel": row["channel"] or "",
                "code_found": row["code_found"],
                "duration_ms": int(row["duration_ms"] or 0),
            }
        )
    timeline.sort(key=lambda item: str(item.get("time") or ""), reverse=True)

    return {
        "kpi": {
            "audit_ops_24h": len(audit_rows),
            "notification_total_24h": len(notification_rows),
            "verification_events_24h": len(verification_rows),
        },
        "notification_stats": notification_stats,
        "op_type_dist": [
            {"action_group": key, "count": count}
            for key, count in sorted(op_type_map.items(), key=lambda item: (-item[1], item[0]))
        ],
        "timeline": timeline[: max(int(timeline_limit), 1)],
    }
