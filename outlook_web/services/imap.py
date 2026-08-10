from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.header import decode_header
from typing import Any, Dict, List, Optional

import requests

from outlook_web.errors import build_error_payload
from outlook_web.services.graph import get_access_token_graph
from outlook_web.services.http import get_response_details

_LOGGER = logging.getLogger(__name__)

# Token 端点
TOKEN_URL_IMAP = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"

# IMAP 服务器配置
IMAP_SERVER_NEW = "outlook.live.com"
IMAP_PORT = 993

_token_cache: Dict[str, tuple] = {}
_token_cache_lock = threading.Lock()


def decode_header_value(header_value: str) -> str:
    """解码邮件头字段"""
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(str(header_value))
        decoded_string = ""
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                try:
                    decoded_string += part.decode(charset if charset else "utf-8", "replace")
                except (LookupError, UnicodeDecodeError):
                    decoded_string += part.decode("utf-8", "replace")
            else:
                decoded_string += str(part)
        return decoded_string
    except Exception:
        return str(header_value) if header_value else ""


def get_email_body(msg) -> str:
    """提取邮件正文"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
                except Exception:
                    continue
            elif content_type == "text/html" and "attachment" not in content_disposition and not body:
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                except Exception:
                    continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
        except Exception:
            body = str(msg.get_payload())

    return body


def _select_folder(connection, folder: str) -> Optional[str]:
    folder_map = {
        "inbox": ["INBOX"],
        "junk": ["Junk", "Junk Email", "Spam", "垃圾邮件"],
        "junkemail": ["Junk", "Junk Email", "Spam", "垃圾邮件"],
        "deleteditems": ["Deleted", "Deleted Items", "Trash", "已删除邮件"],
        "trash": ["Deleted", "Deleted Items", "Trash", "已删除邮件"],
    }
    candidates = folder_map.get((folder or "").lower(), [folder or "INBOX"])
    for candidate in candidates:
        for select_target in (f'"{candidate}"', candidate):
            try:
                status, _ = connection.select(select_target, readonly=True)
                if status == "OK":
                    return candidate
            except Exception:
                continue
    return None


def _get_html_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _parse_batch_fetch_response(all_data: list) -> List[tuple]:
    results = []
    for item in all_data:
        header = None
        raw_email = None

        if isinstance(item, tuple) and len(item) == 2:
            first, second = item
            if isinstance(first, (bytes, bytearray)) and isinstance(second, (bytes, bytearray)):
                header = bytes(first)
                raw_email = bytes(second)
            elif isinstance(first, tuple) and len(first) == 2:
                nested_header, nested_raw = first
                if isinstance(nested_header, (bytes, bytearray)) and isinstance(nested_raw, (bytes, bytearray)):
                    header = bytes(nested_header)
                    raw_email = bytes(nested_raw)

        if not isinstance(header, (bytes, bytearray)) or not isinstance(raw_email, (bytes, bytearray)):
            continue

        msg_id_str = header.split(b" ", 1)[0].decode("ascii", errors="ignore").strip()
        if not msg_id_str:
            continue
        results.append((msg_id_str, raw_email))
    return results


def _make_cache_key(client_id: str, refresh_token: str) -> str:
    rt_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:16]
    return f"{client_id}:{rt_hash}"


def clear_imap_token_cache(client_id: str = None) -> None:
    with _token_cache_lock:
        if client_id is None:
            _token_cache.clear()
        else:
            keys_to_remove = [k for k in _token_cache if k.startswith(f"{client_id}:")]
            for key in keys_to_remove:
                del _token_cache[key]


def get_access_token_imap_result(client_id: str, refresh_token: str) -> Dict[str, Any]:
    """获取 IMAP access_token（包含错误详情）"""
    cache_key = _make_cache_key(client_id, refresh_token)
    with _token_cache_lock:
        cached = _token_cache.get(cache_key)
        if cached:
            access_token, expires_at = cached
            if time.monotonic() < expires_at:
                return {"success": True, "access_token": access_token}

    try:
        res = requests.post(
            TOKEN_URL_IMAP,
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
            },
            timeout=30,
        )

        if res.status_code != 200:
            details = get_response_details(res)
            return {
                "success": False,
                "error": build_error_payload(
                    "IMAP_TOKEN_FAILED",
                    "获取访问令牌失败",
                    "IMAPError",
                    res.status_code,
                    details,
                ),
            }

        payload = res.json()
        access_token = payload.get("access_token")
        if not access_token:
            return {
                "success": False,
                "error": build_error_payload(
                    "IMAP_TOKEN_MISSING",
                    "获取访问令牌失败",
                    "IMAPError",
                    res.status_code,
                    payload,
                ),
            }

        expires_in = int(payload.get("expires_in", 3599))
        ttl = max(0, expires_in - 60)
        with _token_cache_lock:
            _token_cache[cache_key] = (access_token, time.monotonic() + ttl)

        return {"success": True, "access_token": access_token}
    except Exception as exc:
        return {
            "success": False,
            "error": build_error_payload(
                "IMAP_TOKEN_EXCEPTION",
                "获取访问令牌失败",
                type(exc).__name__,
                500,
                str(exc),
            ),
        }


def get_access_token_imap(client_id: str, refresh_token: str) -> Optional[str]:
    """获取 IMAP access_token"""
    result = get_access_token_imap_result(client_id, refresh_token)
    if result.get("success"):
        return result.get("access_token")
    return None


def get_emails_imap(
    account: str,
    client_id: str,
    refresh_token: str,
    folder: str = "inbox",
    skip: int = 0,
    top: int = 20,
) -> Dict[str, Any]:
    """使用 IMAP 获取邮件列表（支持分页和文件夹选择）- 默认使用新版服务器"""
    return get_emails_imap_with_server(account, client_id, refresh_token, folder, skip, top, IMAP_SERVER_NEW)


def get_emails_imap_with_server(
    account: str,
    client_id: str,
    refresh_token: str,
    folder: str = "inbox",
    skip: int = 0,
    top: int = 20,
    server: str = IMAP_SERVER_NEW,
) -> Dict[str, Any]:
    """使用 IMAP 获取邮件列表（支持分页、文件夹选择和服务器选择）"""
    token_result = get_access_token_imap_result(client_id, refresh_token)
    if not token_result.get("success"):
        return {"success": False, "error": token_result.get("error")}

    access_token = token_result.get("access_token")

    connection = None
    try:
        connection = imaplib.IMAP4_SSL(server, IMAP_PORT)
        auth_string = f"user={account}\1auth=Bearer {access_token}\1\1".encode("utf-8")
        connection.authenticate("XOAUTH2", lambda x: auth_string)

        selected_folder = _select_folder(connection, folder)

        if not selected_folder:
            try:
                status, folder_list = connection.list()
                available_folders = []
                if status == "OK" and folder_list:
                    for folder_item in folder_list:
                        if isinstance(folder_item, bytes):
                            available_folders.append(folder_item.decode("utf-8", errors="ignore"))
                        else:
                            available_folders.append(str(folder_item))

                error_details = {
                    "last_error": "select folder failed",
                    "tried_folder": folder,
                    "available_folders": available_folders[:10],
                }
            except Exception:
                error_details = {
                    "last_error": "select folder failed",
                    "tried_folder": folder,
                }

            return {
                "success": False,
                "error": build_error_payload(
                    "EMAIL_FETCH_FAILED",
                    "无法访问文件夹，请检查账号配置",
                    "IMAPSelectError",
                    500,
                    error_details,
                ),
            }

        status, messages = connection.search(None, "ALL")
        if status != "OK":
            _LOGGER.debug(
                "[PERF] imap_search | account=%s | server=%s | folder=%s | status=%s (非OK)",
                account,
                server,
                selected_folder,
                status,
            )
            return {
                "success": False,
                "error": build_error_payload(
                    "EMAIL_FETCH_FAILED",
                    "获取邮件失败，请检查账号配置",
                    "IMAPSearchError",
                    500,
                    f"search status={status}",
                ),
            }
        if not messages or not messages[0]:
            _LOGGER.debug(
                "[PERF] imap_search | account=%s | server=%s | folder=%s | total=0 (空信箱)",
                account,
                server,
                selected_folder,
            )
            return {"success": True, "emails": []}

        message_ids = messages[0].split()
        total = len(message_ids)
        start_idx = max(0, total - skip - top)
        end_idx = total - skip

        _LOGGER.debug(
            "[PERF] imap_search | account=%s | server=%s | folder=%s | total=%d | skip=%d | top=%d | slice=[%d:%d]",
            account,
            server,
            selected_folder,
            total,
            skip,
            top,
            start_idx,
            end_idx,
        )

        if start_idx >= end_idx:
            return {"success": True, "emails": []}

        paged_ids = message_ids[start_idx:end_idx][::-1]
        emails_data = []

        ids_str = b",".join(paged_ids)
        status, all_data = connection.fetch(ids_str, "(RFC822)")
        if status != "OK":
            _LOGGER.debug(
                "[PERF] imap_fetch | account=%s | batch fetch失败 status=%s",
                account,
                status,
            )
            return {"success": True, "emails": emails_data}

        for msg_id_str, raw_email in _parse_batch_fetch_response(all_data or []):
            try:
                msg = email.message_from_bytes(raw_email)
                body_preview = get_email_body(msg)
                emails_data.append(
                    {
                        "id": msg_id_str,
                        "subject": decode_header_value(msg.get("Subject", "无主题")),
                        "from": decode_header_value(msg.get("From", "未知发件人")),
                        "date": msg.get("Date", "未知时间"),
                        "body_preview": (body_preview[:200] + "..." if len(body_preview) > 200 else body_preview),
                    }
                )
            except Exception as fetch_err:
                _LOGGER.debug(
                    "[PERF] imap_fetch | account=%s | msg_id=%s | 解析失败: %s",
                    account,
                    msg_id_str,
                    fetch_err,
                )
                continue

        _LOGGER.debug(
            "[PERF] imap_result | account=%s | server=%s | fetched=%d / requested=%d",
            account,
            server,
            len(emails_data),
            len(paged_ids),
        )
        return {"success": True, "emails": emails_data}
    except Exception as exc:
        return {
            "success": False,
            "error": build_error_payload(
                "EMAIL_FETCH_FAILED",
                "获取邮件失败，请检查账号配置",
                type(exc).__name__,
                500,
                str(exc),
            ),
        }
    finally:
        if connection:
            try:
                connection.logout()
            except Exception:
                pass


def fetch_and_detail_imap_with_server(
    account: str,
    client_id: str,
    refresh_token: str,
    folder: str = "inbox",
    skip: int = 0,
    top: int = 1,
    server: str = IMAP_SERVER_NEW,
) -> Dict[str, Any]:
    """一次 IMAP 连接完成邮件列表 + 最新一封详情。"""
    token_result = get_access_token_imap_result(client_id, refresh_token)
    if not token_result.get("success"):
        return {
            "success": False,
            "error": token_result.get("error"),
            "emails": [],
            "detail": None,
        }

    access_token = token_result["access_token"]
    connection = None

    try:
        connection = imaplib.IMAP4_SSL(server, IMAP_PORT)
        auth_string = f"user={account}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")
        connection.authenticate("XOAUTH2", lambda x: auth_string)

        selected = _select_folder(connection, folder)
        if not selected:
            return {
                "success": False,
                "error": build_error_payload("FOLDER_NOT_FOUND", "文件夹选择失败", "IMAPError", 500, ""),
                "emails": [],
                "detail": None,
            }

        status, messages = connection.search(None, "ALL")
        if status != "OK" or not messages or not messages[0]:
            return {"success": True, "emails": [], "detail": None}

        message_ids = messages[0].split()
        total = len(message_ids)
        start_idx = max(0, total - skip - top)
        end_idx = total - skip
        if start_idx >= end_idx:
            return {"success": True, "emails": [], "detail": None}

        paged_ids = message_ids[start_idx:end_idx][::-1]
        emails_data: List[Dict[str, Any]] = []
        detail = None

        ids_str = b",".join(paged_ids)
        status, all_data = connection.fetch(ids_str, "(RFC822)")
        if status != "OK":
            return {"success": True, "emails": [], "detail": None}

        for i, (msg_id_str, raw_email) in enumerate(_parse_batch_fetch_response(all_data or [])):
            msg = email.message_from_bytes(raw_email)
            body_preview = get_email_body(msg)
            email_item = {
                "id": msg_id_str,
                "subject": decode_header_value(msg.get("Subject", "无主题")),
                "from": decode_header_value(msg.get("From", "未知发件人")),
                "date": msg.get("Date", "未知时间"),
                "body_preview": body_preview[:200] + "..." if len(body_preview) > 200 else body_preview,
            }
            emails_data.append(email_item)

            if i == 0:
                raw_text = raw_email.decode("utf-8", errors="replace") if isinstance(raw_email, (bytes, bytearray)) else ""
                detail = {
                    "id": email_item["id"],
                    "subject": email_item["subject"],
                    "from": email_item["from"],
                    "to": decode_header_value(msg.get("To", "")),
                    "cc": decode_header_value(msg.get("Cc", "")),
                    "date": email_item["date"],
                    "body": get_email_body(msg),
                    "body_html": _get_html_body(msg),
                    "raw_content": raw_text,
                }

        return {"success": True, "emails": emails_data, "detail": detail}
    except imaplib.IMAP4.error as exc:
        return {
            "success": False,
            "error": build_error_payload("AUTH_FAILED", "IMAP认证失败", "IMAP4Error", 401, str(exc)),
            "emails": [],
            "detail": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": build_error_payload(
                "EMAIL_FETCH_FAILED",
                "获取邮件失败",
                type(exc).__name__,
                500,
                str(exc),
            ),
            "emails": [],
            "detail": None,
        }
    finally:
        if connection:
            try:
                connection.logout()
            except Exception:
                pass


def get_emails_imap_concurrent(
    account: str,
    client_id: str,
    refresh_token: str,
    folder: str = "inbox",
    skip: int = 0,
    top: int = 20,
    servers: tuple = (IMAP_SERVER_NEW, "outlook.office365.com"),
) -> Dict[str, Any]:
    """并发连接多台 IMAP 服务器，返回第一个成功结果。"""
    if len(servers) <= 1:
        return get_emails_imap_with_server(
            account,
            client_id,
            refresh_token,
            folder,
            skip,
            top,
            servers[0] if servers else IMAP_SERVER_NEW,
        )

    last_error = None
    with ThreadPoolExecutor(max_workers=len(servers)) as executor:
        futures = {
            executor.submit(
                get_emails_imap_with_server,
                account,
                client_id,
                refresh_token,
                folder,
                skip,
                top,
                server,
            ): server
            for server in servers
        }
        for future in as_completed(futures):
            result = future.result()
            if result.get("success"):
                return result
            last_error = result

    return last_error or {
        "success": False,
        "error": {"code": "ALL_SERVERS_FAILED", "message": "所有服务器连接失败"},
    }


def get_email_detail_imap(
    account: str,
    client_id: str,
    refresh_token: str,
    message_id: str,
    folder: str = "inbox",
) -> Optional[Dict]:
    """使用 IMAP 获取邮件详情（默认使用新版服务器）。"""
    return get_email_detail_imap_with_server(account, client_id, refresh_token, message_id, folder, IMAP_SERVER_NEW)


def get_email_detail_imap_with_server(
    account: str,
    client_id: str,
    refresh_token: str,
    message_id: str,
    folder: str = "inbox",
    server: str = IMAP_SERVER_NEW,
) -> Optional[Dict]:
    """使用 IMAP 获取邮件详情（支持指定服务器）。"""
    access_token = get_access_token_imap(client_id, refresh_token)
    if not access_token:
        return None

    connection = None
    try:
        connection = imaplib.IMAP4_SSL(server, IMAP_PORT)
        auth_string = f"user={account}\1auth=Bearer {access_token}\1\1".encode("utf-8")
        connection.authenticate("XOAUTH2", lambda x: auth_string)

        folder_map = {
            "inbox": ['"INBOX"', "INBOX"],
            "junkemail": ['"Junk"', '"Junk Email"', "Junk", '"垃圾邮件"'],
            "deleteditems": [
                '"Deleted"',
                '"Deleted Items"',
                '"Trash"',
                "Deleted",
                '"已删除邮件"',
            ],
            "trash": [
                '"Deleted"',
                '"Deleted Items"',
                '"Trash"',
                "Deleted",
                '"已删除邮件"',
            ],
        }
        possible_folders = folder_map.get((folder or "").lower(), ['"INBOX"'])

        selected_folder = None
        for imap_folder in possible_folders:
            try:
                status, response = connection.select(imap_folder, readonly=True)
                if status == "OK":
                    selected_folder = imap_folder
                    break
            except Exception:
                continue

        if not selected_folder:
            return None

        fetch_id = message_id.encode() if isinstance(message_id, str) else message_id
        status, msg_data = connection.fetch(fetch_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            return None

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        raw_text = ""
        try:
            raw_text = raw_email.decode("utf-8", errors="replace") if isinstance(raw_email, (bytes, bytearray)) else ""
        except Exception:
            raw_text = ""

        return {
            "id": message_id,
            "subject": decode_header_value(msg.get("Subject", "无主题")),
            "from": decode_header_value(msg.get("From", "未知发件人")),
            "to": decode_header_value(msg.get("To", "")),
            "cc": decode_header_value(msg.get("Cc", "")),
            "date": msg.get("Date", "未知时间"),
            "body": get_email_body(msg),
            "raw_content": raw_text,
        }
    except Exception:
        return None
    finally:
        if connection:
            try:
                connection.logout()
            except Exception:
                pass


def _internet_message_id_candidates(internet_message_id: str) -> List[str]:
    raw = (internet_message_id or "").strip()
    if not raw:
        return []
    candidates = [raw]
    if raw.startswith("<") and raw.endswith(">"):
        candidates.append(raw[1:-1])
    else:
        candidates.append(f"<{raw}>")
    # 去重且保持顺序
    seen = set()
    ordered: List[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _resolve_internet_message_ids(
    *,
    graph_access_token: str,
    message_ids: List[str],
) -> tuple[List[str], List[str], List[str]]:
    """
    将 Graph message id 解析为 RFC Message-ID。
    返回: (internet_message_ids, already_gone_ids, resolve_errors)
    """
    from urllib.parse import quote

    headers = {"Authorization": f"Bearer {graph_access_token}"}
    resolved: List[str] = []
    already_gone: List[str] = []
    errors: List[str] = []

    for msg_id in message_ids:
        mid = (msg_id or "").strip()
        if not mid:
            continue
        # 兼容：调用方若直接传入 Message-ID / <Message-ID>
        if "@" in mid and " " not in mid and len(mid) < 300:
            resolved.extend(_internet_message_id_candidates(mid)[:1])
            continue
        try:
            url = f"https://graph.microsoft.com/v1.0/me/messages/{quote(mid, safe='')}"
            resp = requests.get(
                url,
                headers=headers,
                params={"$select": "id,internetMessageId"},
                timeout=30,
            )
            if resp.status_code == 404:
                already_gone.append(mid)
                continue
            if resp.status_code != 200:
                errors.append(f"Msg ID: {mid}, resolve_status={resp.status_code}")
                continue
            internet_id = str((resp.json() or {}).get("internetMessageId") or "").strip()
            if not internet_id:
                errors.append(f"Msg ID: {mid}, missing internetMessageId")
                continue
            resolved.append(internet_id)
        except Exception as exc:
            errors.append(f"Msg ID: {mid}, resolve_error={exc}")

    return resolved, already_gone, errors


def _imap_select_writable(connection: imaplib.IMAP4, folder: str) -> Optional[str]:
    folder_map = {
        "inbox": ["INBOX"],
        "junk": ["Junk", "Junk Email", "Spam", "垃圾邮件"],
        "junkemail": ["Junk", "Junk Email", "Spam", "垃圾邮件"],
        "deleteditems": ["Deleted", "Deleted Items", "Trash", "已删除邮件"],
        "trash": ["Deleted", "Deleted Items", "Trash", "已删除邮件"],
    }
    candidates = folder_map.get((folder or "").lower(), [folder or "INBOX"])
    for candidate in candidates:
        for select_target in (f'"{candidate}"', candidate):
            try:
                status, _ = connection.select(select_target, readonly=False)
                if status == "OK":
                    return candidate
            except Exception:
                continue
    return None


def delete_emails_imap(
    email_addr: str,
    client_id: str,
    refresh_token: str,
    message_ids: List[str],
    server: str,
) -> Dict[str, Any]:
    """
    通过 IMAP 删除邮件（永久删除）。

    Graph message id 与 IMAP UID 不兼容：先用 Graph(Mail.Read) 解析 internetMessageId，
    再在 IMAP 中按 Message-ID 定位并 STORE \\Deleted + EXPUNGE。
    适用于 token 仅有 Mail.Read、没有 Mail.ReadWrite 导致 Graph DELETE 403 的场景。
    """
    if not message_ids:
        return {"success": True, "success_count": 0, "failed_count": 0, "errors": []}

    graph_token = get_access_token_graph(client_id, refresh_token)
    if not graph_token:
        return {
            "success": False,
            "error": build_error_payload(
                "GRAPH_TOKEN_FAILED",
                "获取 Graph 访问令牌失败，无法解析邮件 ID",
                "IMAPError",
                500,
                "empty_graph_token",
            ),
        }

    internet_ids, already_gone, resolve_errors = _resolve_internet_message_ids(
        graph_access_token=graph_token,
        message_ids=message_ids,
    )

    token_result = get_access_token_imap_result(client_id, refresh_token)
    if not token_result.get("success"):
        return {
            "success": False,
            "error": token_result.get("error")
            or build_error_payload("IMAP_TOKEN_FAILED", "获取 IMAP 访问令牌失败", "IMAPError", 500, ""),
            "success_count": len(already_gone),
            "failed_count": max(0, len(message_ids) - len(already_gone)),
            "errors": resolve_errors,
        }

    access_token = token_result.get("access_token")
    success_count = len(already_gone)
    failed_count = 0
    errors: List[str] = list(resolve_errors)
    imap = None

    try:
        auth_string = "user=%s\x01auth=Bearer %s\x01\x01" % (email_addr, access_token)
        imap = imaplib.IMAP4_SSL(server, IMAP_PORT)
        imap.authenticate("XOAUTH2", lambda x: auth_string.encode("utf-8"))

        search_folders = ("inbox", "junk", "deleteditems")
        for internet_id in internet_ids:
            deleted = False
            last_err = ""
            for folder_key in search_folders:
                selected = _imap_select_writable(imap, folder_key)
                if not selected:
                    continue
                for candidate in _internet_message_id_candidates(internet_id):
                    try:
                        typ, data = imap.uid("SEARCH", None, "HEADER", "Message-ID", candidate)
                    except Exception as exc:
                        last_err = str(exc)
                        continue
                    if typ != "OK" or not data or not data[0]:
                        continue
                    uids = data[0].split()
                    if not uids:
                        continue
                    for uid in uids:
                        try:
                            store_typ, _ = imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
                            if store_typ != "OK":
                                last_err = f"STORE failed for uid={uid}"
                                continue
                            deleted = True
                        except Exception as exc:
                            last_err = str(exc)
                    if deleted:
                        try:
                            imap.expunge()
                        except Exception:
                            pass
                        break
                if deleted:
                    break
            if deleted:
                success_count += 1
            else:
                failed_count += 1
                errors.append(
                    f"Message-ID: {internet_id}, IMAP 未找到或删除失败"
                    + (f" ({last_err})" if last_err else "")
                )

        # 解析失败的也计入失败
        failed_count += len(resolve_errors)

        result: Dict[str, Any] = {
            "success": success_count > 0,
            "partial_success": success_count > 0 and failed_count > 0,
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors,
        }
        if not result["success"]:
            result["error"] = build_error_payload(
                "EMAIL_DELETE_FAILED",
                "IMAP 删除邮件失败",
                "IMAPError",
                502,
                {"failed_count": failed_count, "errors": errors[:10]},
            )
        return result
    except Exception as e:
        return {
            "success": False,
            "error": build_error_payload(
                "EMAIL_DELETE_FAILED",
                "IMAP 删除邮件失败",
                type(e).__name__,
                500,
                str(e),
            ),
            "success_count": success_count,
            "failed_count": max(failed_count, len(message_ids) - success_count),
            "errors": errors + [str(e)],
        }
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
