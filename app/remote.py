"""他の端末・他の人からのアクセスと、アクセスキーによる保護。

- 管理者キー (access_key.txt / STEMSCORE_ACCESS_KEY) で開いた人は管理者。全員の曲を見られ、メンバーを招待できる
- メンバーは管理者が発行した招待キーで開く。自分の曲だけが見える
- この PC 自身 (127.0.0.1) からのアクセスは管理者扱いでキー不要 (クラウド公開時を除く)
- キーは QR コード / URL (?key=...) で一度開くと Cookie に保存される
"""
from __future__ import annotations

import contextvars
import io
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config

KEY_FILE = config.DATA / "access_key.txt"
MEMBERS_FILE = config.DATA / "members.json"
ADMIN = {"id": "admin", "name": "管理者", "admin": True}
# リクエスト中のユーザー (ミドルウェアで設定し、API の処理から参照する)
CURRENT_USER: contextvars.ContextVar[dict | None] = contextvars.ContextVar("current_user", default=None)
_members_lock = threading.Lock()


def current_user() -> dict:
    return CURRENT_USER.get() or ADMIN


def _load_members() -> list[dict]:
    try:
        return json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_members(members: list[dict]) -> None:
    MEMBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMBERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(members, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(MEMBERS_FILE)


def list_members() -> list[dict]:
    return _load_members()


def add_member(name: str) -> dict:
    with _members_lock:
        members = _load_members()
        m = {"id": secrets.token_hex(4), "name": name.strip()[:30] or "メンバー",
             "key": secrets.token_urlsafe(9), "created": time.time()}
        members.append(m)
        _save_members(members)
    return m


def remove_member(mid: str) -> bool:
    with _members_lock:
        members = _load_members()
        rest = [m for m in members if m["id"] != mid]
        _save_members(rest)
    return len(rest) != len(members)


def user_for_key(key: str) -> dict | None:
    if not key:
        return None
    if secrets.compare_digest(key, access_key()):
        return ADMIN
    for m in _load_members():
        if secrets.compare_digest(key, m["key"]):
            return {"id": m["id"], "name": m["name"], "admin": False}
    return None
COOKIE = "stemscore_key"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def access_key() -> str:
    # クラウド (Render など) では環境変数で指定したキーを使う
    if os.environ.get("STEMSCORE_ACCESS_KEY"):
        return os.environ["STEMSCORE_ACCESS_KEY"].strip()
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(secrets.token_urlsafe(9), encoding="utf-8")
    return KEY_FILE.read_text(encoding="utf-8").strip()


def reset_key() -> str:
    KEY_FILE.unlink(missing_ok=True)
    return access_key()


def lan_ips() -> list[str]:
    ips: list[str] = []
    # 既定ルートに使われる IP を最優先 (実際には通信しない)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.append(ip)
    except OSError:
        pass
    return ips


def is_local(request: Request) -> bool:
    # クラウド公開時はトンネル経由のアクセスも 127.0.0.1 から届くため、常にキーを要求する
    if config.CLOUD:
        return False
    return (request.client.host if request.client else "") in LOCAL_HOSTS


def qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def qr_terminal(text: str) -> str:
    import qrcode

    q = qrcode.QRCode(border=1)
    q.add_data(text)
    buf = io.StringIO()
    q.print_ascii(out=buf, invert=True)
    return buf.getvalue()


async def auth_middleware(request: Request, call_next):
    """アクセスキー (管理者 / メンバー) を確認し、リクエスト中のユーザーを設定する。"""
    path = request.url.path
    # マニフェスト/アイコンはホーム画面追加やクラウドの稼働確認のため公開
    if path in ("/manifest.webmanifest", "/sw.js") or path.startswith("/static/icons/"):
        return await call_next(request)
    if is_local(request):
        CURRENT_USER.set(ADMIN)
        return await call_next(request)
    q = request.query_params.get("key")
    if q is not None:
        if user_for_key(q):
            # キーを Cookie に保存し、URL からキーを消してリダイレクト
            # クラウドのプロキシ経由でも正しく戻れるよう、ホスト名を含まない相対 URL でリダイレクト
            url = request.url.remove_query_params("key")
            target = url.path + (f"?{url.query}" if url.query else "")
            resp = RedirectResponse(target, status_code=303)
            resp.set_cookie(COOKIE, q, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https")
            return resp
    else:
        user = user_for_key(request.cookies.get(COOKIE, ""))
        if user:
            CURRENT_USER.set(user)
            return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "アクセスキーが必要です (招待が取り消された可能性があります)"}, status_code=401)
    return HTMLResponse(LOCKED_PAGE, status_code=401)


LOCKED_PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>StemScore</title>
<style>body{font-family:system-ui,sans-serif;background:#121219;color:#ececf4;display:grid;place-items:center;
min-height:100vh;margin:0;padding:16px;box-sizing:border-box}form{max-width:360px;width:100%;text-align:center}
input{width:100%;box-sizing:border-box;padding:12px;font-size:16px;border-radius:10px;border:1px solid #444;
background:#1b1b25;color:inherit;margin:12px 0}button{width:100%;padding:12px;font-size:16px;border:0;
border-radius:10px;background:#8b7cff;color:#fff;font-weight:600}p{color:#9a9ab0;font-size:14px}</style></head>
<body><form method="get" action="/"><h1>🔒 StemScore</h1>
<p>招待された QR コード / リンクから開くか、アクセスキーを入力してください。</p>
<input name="key" placeholder="アクセスキー" autocomplete="off" autocapitalize="off">
<button>開く</button></form></body></html>"""
