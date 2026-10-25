from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from outlook_web.errors import build_error_payload


def summarize_fallback_failures(method_errors: Dict[str, Any], labels: Dict[str, str]) -> str:
    """将多方式回退的失败原因聚合成“中文可理解”的摘要文本（用于 error.details 展示）。"""
    lines: List[str] = []

    for key, label in labels.items():
        if key not in method_errors:
            continue
        err = method_errors.get(key)
        if err is None:
            text = "未知错误"
        elif isinstance(err, dict):
            msg = (err.get("message") or err.get("error") or "").strip()
            code = (err.get("code") or "").strip()
            status = err.get("status")
            meta_parts: List[str] = []
            if code:
                meta_parts.append(f"code={code}")
            if status:
                meta_parts.append(f"status={status}")
            meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
            if msg:
                text = f"{msg}{meta}"
            else:
                raw = json.dumps(err, ensure_ascii=False)
                text = raw[:400] + ("..." if len(raw) > 400 else "")
        elif isinstance(err, list):
            preview_items = [str(x) for x in err[:3]]
            preview = "; ".join(preview_items)
            if len(err) > 3:
                preview += f" ...(共 {len(err)} 条)"
            text = preview
        else:
            text = str(err)

        lines.append(f"{label}：{text}")

    return "\n".join(lines).strip()


def delete_emails_with_fallback(
    *,
    email_addr: str,
    client_id: str,
    refresh_token: str,
    message_ids: List[str],
    proxy_url: str,
    delete_emails_graph: Callable[[str, str, List[str], Optional[str]], Dict[str, Any]],
    delete_emails_imap: Callable[[str, str, str, List[str], str], Dict[str, Any]],
    imap_server_new: str,
    imap_server_old: str,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    删除邮件（Graph 优先，IMAP 回退）并保持对外错误聚合结构一致。

    返回: (response_data, method)；method 为 'graph'/'imap_new'/'imap_old' 或 None。
    """
    graph_res = delete_emails_graph(client_id, refresh_token, message_ids, proxy_url)
    if graph_res.get("success"):
        return graph_res, "graph"

    graph_error = graph_res.get("error")
    graph_errors_list = graph_res.get("errors") or []
    is_proxy_error = (
        (isinstance(graph_error, dict) and graph_error.get("type") in ("ProxyError", "ConnectionError"))
        or (isinstance(graph_error, str) and "ProxyError" in graph_error)
        or any("ProxyError" in str(x) for x in graph_errors_list[:5])
    )
    if is_proxy_error:
        return graph_res, None

    method_errors: Dict[str, Any] = {
        "graph": graph_error or graph_errors_list or "Graph API 删除失败",
    }

    imap_res = delete_emails_imap(email_addr, client_id, refresh_token, message_ids, imap_server_new)
    if imap_res.get("success"):
        return imap_res, "imap_new"
    method_errors["imap_new"] = imap_res.get("error") or imap_res

    imap_old_res = delete_emails_imap(email_addr, client_id, refresh_token, message_ids, imap_server_old)
    if imap_old_res.get("success"):
        return imap_old_res, "imap_old"
    method_errors["imap_old"] = imap_old_res.get("error") or imap_old_res

    summary = summarize_fallback_failures(
        method_errors,
        {
            "graph": "Graph API",
            "imap_new": "IMAP（新服务器）",
            "imap_old": "IMAP（旧服务器）",
        },
    )
    error_payload = build_error_payload(
        "EMAIL_DELETE_ALL_METHODS_FAILED",
        "删除邮件失败，所有方式均失败",
        "FallbackError",
        502,
        summary,
    )

    response_data = dict(graph_res)
    response_data.setdefault("success_count", 0)
    response_data.setdefault("failed_count", len(message_ids))
    response_data.setdefault("errors", [])
    response_data.update(
        {
            "success": False,
            "error": error_payload,
            "details": method_errors,
        }
    )
    return response_data, None


def _email_matches_keyword(email_item: Dict[str, Any], keyword_lower: str) -> bool:
    subject = str(email_item.get("subject") or "")
    preview = str(email_item.get("bodyPreview") or "")
    return keyword_lower in subject.lower() or keyword_lower in preview.lower()


def collect_message_ids_for_purge(
    *,
    get_emails_fn: Callable[..., Dict[str, Any]],
    client_id: str,
    refresh_token: str,
    folders: List[str],
    keyword: Optional[str] = None,
    proxy_url: str = "",
    page_size: int = 50,
    max_messages: int = 2000,
) -> Tuple[List[str], Optional[Dict[str, Any]], Optional[str]]:
    """分页收集待删除邮件 ID。keyword 为空表示清空；匹配主题或正文预览（不区分大小写）。"""
    keyword_lower = (keyword or "").strip().lower()
    collected: List[str] = []
    seen: set[str] = set()
    latest_refresh_token: Optional[str] = None

    for folder in folders:
        skip = 0
        while len(collected) < max_messages:
            result = get_emails_fn(
                client_id,
                refresh_token,
                folder=folder,
                skip=skip,
                top=page_size,
                proxy_url=proxy_url or None,
            )
            if not result.get("success"):
                return collected, result.get("error") or {"message": "获取邮件失败"}, latest_refresh_token

            new_rt = result.get("new_refresh_token")
            if new_rt:
                latest_refresh_token = str(new_rt)
                refresh_token = latest_refresh_token

            emails = result.get("emails") or []
            if not emails:
                break

            for item in emails:
                msg_id = str(item.get("id") or "").strip()
                if not msg_id or msg_id in seen:
                    continue
                if keyword_lower and not _email_matches_keyword(item, keyword_lower):
                    continue
                seen.add(msg_id)
                collected.append(msg_id)
                if len(collected) >= max_messages:
                    break

            if len(emails) < page_size:
                break
            skip += page_size

    return collected, None, latest_refresh_token


def purge_account_emails(
    *,
    email_addr: str,
    client_id: str,
    refresh_token: str,
    folders: List[str],
    keyword: Optional[str],
    proxy_url: str,
    get_emails_fn: Callable[..., Dict[str, Any]],
    delete_emails_graph: Callable[[str, str, List[str], Optional[str]], Dict[str, Any]],
    delete_emails_imap: Callable[[str, str, str, List[str], str], Dict[str, Any]],
    imap_server_new: str,
    imap_server_old: str,
    max_messages: int = 2000,
) -> Dict[str, Any]:
    """对单个账号按关键词或清空方式删除邮件，返回汇总结果。"""
    message_ids, list_error, new_refresh_token = collect_message_ids_for_purge(
        get_emails_fn=get_emails_fn,
        client_id=client_id,
        refresh_token=refresh_token,
        folders=folders,
        keyword=keyword,
        proxy_url=proxy_url,
        max_messages=max_messages,
    )
    if list_error and not message_ids:
        return {
            "success": False,
            "email": email_addr,
            "matched_count": 0,
            "deleted_count": 0,
            "failed_count": 0,
            "error": list_error,
            "new_refresh_token": new_refresh_token,
        }

    if not message_ids:
        return {
            "success": True,
            "email": email_addr,
            "matched_count": 0,
            "deleted_count": 0,
            "failed_count": 0,
            "message": "没有匹配的邮件",
            "new_refresh_token": new_refresh_token,
        }

    active_refresh_token = new_refresh_token or refresh_token
    deleted_count = 0
    failed_count = 0
    method_used: Optional[str] = None
    last_error: Any = None
    chunk_size = 50

    for i in range(0, len(message_ids), chunk_size):
        chunk = message_ids[i : i + chunk_size]
        response_data, method = delete_emails_with_fallback(
            email_addr=email_addr,
            client_id=client_id,
            refresh_token=active_refresh_token,
            message_ids=chunk,
            proxy_url=proxy_url,
            delete_emails_graph=delete_emails_graph,
            delete_emails_imap=delete_emails_imap,
            imap_server_new=imap_server_new,
            imap_server_old=imap_server_old,
        )
        if method:
            method_used = method
        deleted_count += int(response_data.get("success_count") or 0)
        failed_count += int(response_data.get("failed_count") or 0)
        if not response_data.get("success"):
            last_error = response_data.get("error") or last_error

    return {
        "success": deleted_count > 0 or failed_count == 0,
        "email": email_addr,
        "matched_count": len(message_ids),
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "method": method_used,
        "error": last_error,
        "new_refresh_token": new_refresh_token,
        "truncated": list_error is not None,
        "list_error": list_error,
    }
