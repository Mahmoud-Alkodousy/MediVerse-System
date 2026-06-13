"""
MediVerse - API Audit Logging Middleware
Logs EVERY request & response to the api_logs table.
device_id = fingerprint from IP + User-Agent (same device = same id always)
device_name = custom name from X-Device-Name header
"""

import time
import uuid
import json
import re
import hashlib
import logging
import sys

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from config.settings import settings
from middleware.rate_limiter import rate_limiter

logger = logging.getLogger("mediverse")

SKIP_ENDPOINTS = {"/health", "/favicon.ico", "/docs", "/openapi.json", "/redoc"}
SKIP_PREFIXES = ("/docs", "/openapi", "/manager/api-logs")

MAX_BODY_SIZE = 50_000
SENSITIVE_FIELDS = re.compile(r'"(password|password_hash|access_token|refresh_token|secret)":\s*"[^"]*"', re.IGNORECASE)


def _parse_user_agent(ua: str) -> dict:
    ua = ua or ""
    result = {"device_type": "Unknown", "browser": "Unknown", "os": "Unknown"}

    if any(k in ua.lower() for k in ["mobile", "android", "iphone", "ipad"]):
        result["device_type"] = "Tablet" if ("ipad" in ua.lower() or "tablet" in ua.lower()) else "Mobile"
    elif any(k in ua.lower() for k in ["bot", "crawl", "spider", "curl", "wget", "postman", "insomnia"]):
        result["device_type"] = "Bot/Tool"
    elif ua:
        result["device_type"] = "Desktop"

    if "Edg/" in ua:
        m = re.search(r"Edg/([\d.]+)", ua)
        result["browser"] = f"Edge {m.group(1)}" if m else "Edge"
    elif "Chrome/" in ua and "Edg/" not in ua:
        m = re.search(r"Chrome/([\d.]+)", ua)
        result["browser"] = f"Chrome {m.group(1).split('.')[0]}" if m else "Chrome"
    elif "Firefox/" in ua:
        m = re.search(r"Firefox/([\d.]+)", ua)
        result["browser"] = f"Firefox {m.group(1).split('.')[0]}" if m else "Firefox"
    elif "Safari/" in ua and "Chrome/" not in ua:
        result["browser"] = "Safari"
    elif "Postman" in ua:
        result["browser"] = "Postman"
    elif "curl" in ua:
        result["browser"] = "cURL"
    elif "Dart" in ua or "Flutter" in ua:
        result["browser"] = "Flutter/Dart"
    elif "okhttp" in ua:
        result["browser"] = "OkHttp (Android)"

    if "Windows NT 10" in ua:
        result["os"] = "Windows 10/11"
    elif "Windows" in ua:
        result["os"] = "Windows"
    elif "Macintosh" in ua or "Mac OS" in ua:
        result["os"] = "macOS"
    elif "Android" in ua:
        m = re.search(r"Android ([\d.]+)", ua)
        result["os"] = f"Android {m.group(1)}" if m else "Android"
    elif "iPhone" in ua or "iPad" in ua:
        m = re.search(r"OS ([\d_]+)", ua)
        ver = m.group(1).replace("_", ".") if m else ""
        result["os"] = f"iOS {ver}" if ver else "iOS"
    elif "Linux" in ua:
        result["os"] = "Linux"

    return result


def _generate_device_id(ip: str, user_agent: str) -> str:
    """Generate a stable short fingerprint from IP + User-Agent.
    Same device + same browser = same device_id always."""
    raw = f"{ip}|{user_agent}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _mask_sensitive(body_str: str) -> str:
    return SENSITIVE_FIELDS.sub(lambda m: f'"{m.group(1)}": "***MASKED***"', body_str)


def _extract_user_from_token(request: Request) -> dict:
    user_info = {"user_id": None, "user_role": None, "user_name": None}
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            from jose import jwt as jose_jwt
            token = auth.split(" ", 1)[1]
            payload = jose_jwt.decode(
                token, settings.jwt.SECRET_KEY,
                algorithms=[settings.jwt.ALGORITHM],
                options={"verify_exp": False}
            )
            user_info["user_id"] = payload.get("user_id")
            user_info["user_role"] = payload.get("role")
            user_info["user_name"] = payload.get("name")
        except Exception:
            pass
    return user_info


def _save_log(log_data: dict):
    """Insert log entry into api_logs table."""
    try:
        from database.connection import DatabaseManager
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO api_logs (
                    method, endpoint, query_params, status_code, duration_ms,
                    client_ip, user_agent, device_type, browser, os,
                    user_id, user_role, user_name,
                    request_body, request_content_type,
                    response_body, response_size_bytes, error_detail,
                    request_id, device_id, device_name
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                log_data.get("method"),
                log_data.get("endpoint"),
                log_data.get("query_params"),
                log_data.get("status_code"),
                log_data.get("duration_ms"),
                log_data.get("client_ip"),
                log_data.get("user_agent"),
                log_data.get("device_type"),
                log_data.get("browser"),
                log_data.get("os"),
                log_data.get("user_id"),
                log_data.get("user_role"),
                log_data.get("user_name"),
                log_data.get("request_body"),
                log_data.get("request_content_type"),
                log_data.get("response_body"),
                log_data.get("response_size_bytes"),
                log_data.get("error_detail"),
                log_data.get("request_id"),
                log_data.get("device_id"),
                log_data.get("device_name"),
            ))
            cursor.close()
    except Exception as e:
        print(f"[AUDIT ERROR] {e}", file=sys.stderr)
        logger.warning(f"Failed to save API log: {e}")


class RequestLoggingMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        if path in SKIP_ENDPOINTS or any(path.startswith(p) for p in SKIP_PREFIXES):
            return await call_next(request)

        # Skip SSE streaming endpoints — middleware buffers the response which kills streaming
        if path.endswith("-stream") or path.endswith("/stream"):
            return await call_next(request)

        req_id = str(uuid.uuid4())
        request.state.request_id = req_id
        start_time = time.time()

        # Client info
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")
        ua_info = _parse_user_agent(user_agent)
        content_type = request.headers.get("content-type", "")

        # Device identification
        device_id = _generate_device_id(client_ip, user_agent)
        device_name = request.headers.get("x-device-name", None)

        # Rate limiting
        if not rate_limiter.is_allowed(client_ip):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})

        # Read request body
        request_body_str = None
        try:
            if "multipart/form-data" in content_type:
                request_body_str = "[multipart/form-data upload]"
            elif "json" in content_type or request.method in ("POST", "PUT", "PATCH"):
                body_bytes = await request.body()
                if body_bytes and len(body_bytes) <= MAX_BODY_SIZE:
                    request_body_str = _mask_sensitive(body_bytes.decode("utf-8", errors="replace"))
                elif body_bytes:
                    request_body_str = f"[body too large: {len(body_bytes)} bytes]"
                request._body = body_bytes
        except Exception:
            request_body_str = "[failed to read body]"

        # User from JWT
        user_info = _extract_user_from_token(request)

        # Call endpoint
        status_code = 500
        response_body_str = None
        response_size = 0
        error_detail = None

        try:
            response = await call_next(request)
            status_code = response.status_code

            try:
                resp_body_chunks = []
                async for chunk in response.body_iterator:
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    resp_body_chunks.append(chunk)

                resp_bytes = b"".join(resp_body_chunks)
                response_size = len(resp_bytes)

                if response_size <= MAX_BODY_SIZE:
                    resp_text = resp_bytes.decode("utf-8", errors="replace")
                    response_body_str = _mask_sensitive(resp_text)
                    if status_code >= 400:
                        try:
                            error_detail = json.loads(resp_text).get("detail", resp_text[:500])
                        except Exception:
                            error_detail = resp_text[:500]
                else:
                    response_body_str = f"[response too large: {response_size} bytes]"

                response = StreamingResponse(
                    iter([resp_bytes]),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            except Exception as e:
                response_body_str = f"[failed to read response: {e}]"

            return response

        except Exception as exc:
            status_code = 500
            error_detail = str(exc)[:1000]
            raise

        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            query_params = str(request.url.query) if request.url.query else None

            log_data = {
                "request_id": req_id,
                "method": request.method,
                "endpoint": path,
                "query_params": query_params,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip,
                "user_agent": user_agent[:500] if user_agent else None,
                "device_type": ua_info["device_type"],
                "browser": ua_info["browser"],
                "os": ua_info["os"],
                "user_id": user_info["user_id"],
                "user_role": user_info["user_role"],
                "user_name": user_info["user_name"],
                "request_body": request_body_str,
                "request_content_type": content_type[:100] if content_type else None,
                "response_body": response_body_str,
                "response_size_bytes": response_size,
                "error_detail": error_detail,
                "device_id": device_id,
                "device_name": device_name,
            }

            _save_log(log_data)

            if settings.log.LOG_REQUESTS:
                emoji = "✅" if status_code < 400 else "⚠️" if status_code < 500 else "❌"
                dev = f" [{device_name}]" if device_name else ""
                logger.info(
                    f"{emoji} [{req_id[:8]}] {request.method} {path} → {status_code} "
                    f"({duration_ms}ms) | {ua_info['device_type']}/{ua_info['browser']} | "
                    f"IP: {client_ip} | Device: {device_id}{dev} | "
                    f"User: {user_info.get('user_name', 'anonymous')}"
                )
