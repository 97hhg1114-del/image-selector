#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Image Selector - 로컬 이미지 셀렉터 (AI 미사용, 표준 라이브러리만 사용)

실행:  python server.py
브라우저가 자동으로 열립니다.
"""

import http.server
import json
import mimetypes
import os
import posixpath
import secrets
import shutil
import socket
import sys
import threading
import urllib.parse
import webbrowser

def resource_path(name):
    """개발 실행과 PyInstaller exe 양쪽에서 동봉 파일을 찾는다."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_FILE = resource_path("ui.html")

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp",
    ".bmp", ".avif", ".jfif", ".ico", ".tif", ".tiff",
}
MAX_FILES = 3000

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("image/jpeg", ".jfif")

STATE = {"root": None}
STATE_LOCK = threading.Lock()

# ---- 로컬 서버 보호 ----------------------------------------------------
# 127.0.0.1 에만 바인딩하는 것으로는 브라우저를 경유한 공격을 못 막는다.
# 다른 사이트를 띄워 둔 탭이 이 포트로 요청을 보낼 수 있기 때문(CSRF),
# DNS 리바인딩으로 자기 도메인을 127.0.0.1 로 돌려 놓을 수도 있다. 그래서
#   1) 실행할 때마다 새로 만든 토큰을 URL에 실어 브라우저를 열고, 페이지를
#      포함한 모든 요청에서 확인한다. 페이지 본문에 심지 않는 이유: 같은 PC의
#      다른 프로세스·다른 계정이 GET / 한 번으로 토큰을 얻어 가면 안 되니까.
#   2) Host / Origin / Sec-Fetch-Site 헤더로 요청이 이 페이지에서 왔는지 본다.
TOKEN = secrets.token_urlsafe(24)
ALLOWED_HOSTS = set()    # main() 에서 127.0.0.1:<port>, localhost:<port> 로 채운다
ALLOWED_ORIGINS = set()
MAX_BODY = 1 << 20       # JSON 요청 본문 상한(1MB). 경로 목록이 이보다 클 일은 없다


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def scan_folder(root, recursive):
    """root 아래의 이미지 파일 목록을 (상대경로, 크기) 로 반환."""
    items = []
    truncated = False
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in sorted(dirnames)
                           if not d.startswith(".") and d.lower() != "selected"]
            for name in sorted(filenames):
                if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        continue
                    items.append({"path": rel, "name": name, "size": size})
                    if len(items) >= MAX_FILES:
                        truncated = True
                        return items, truncated
    else:
        try:
            names = sorted(os.listdir(root))
        except OSError as exc:
            raise exc
        for name in names:
            full = os.path.join(root, name)
            if os.path.isfile(full) and os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                items.append({"path": name, "name": name, "size": size})
                if len(items) >= MAX_FILES:
                    truncated = True
                    break
    return items, truncated


def resolve_under_root(rel):
    """상대경로를 root 하위 실제 경로로 안전하게 변환."""
    with STATE_LOCK:
        root = STATE["root"]
    if not root:
        return None
    rel = rel.replace("\\", "/")
    rel = posixpath.normpath(rel)
    if rel.startswith("../") or rel == ".." or os.path.isabs(rel):
        return None
    # realpath 로 심볼릭 링크·정션을 먼저 풀어야 root 안의 링크를 타고
    # 바깥 경로로 빠져나가는 것을 막을 수 있다.
    full = os.path.realpath(os.path.join(root, *rel.split("/")))
    real_root = os.path.realpath(root)
    if norm(full) != norm(real_root) and not norm(full).startswith(norm(real_root) + os.sep):
        return None
    return full


def pick_folder_dialog():
    """OS 기본 폴더 선택창 (tkinter). 실패하면 None."""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:
        return None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        path = filedialog.askdirectory(title="이미지 폴더 선택", parent=root)
        root.destroy()
        return path or None
    except Exception:
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ImageSelector/1.0"
    protocol_version = "HTTP/1.1"
    timeout = 30             # 요청을 보내다 마는 연결은 30초 뒤 끊는다

    def log_message(self, fmt, *args):  # 콘솔 조용히
        pass

    # ---------- helpers ----------
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, ctype, cache=True):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 요청 출처 검증 ----------
    def origin_ok(self):
        """브라우저가 붙여 주는 헤더로 이 페이지에서 온 요청인지 확인한다."""
        if (self.headers.get("Host") or "").strip().lower() not in ALLOWED_HOSTS:
            return False                      # DNS 리바인딩 차단
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin and origin not in ALLOWED_ORIGINS:
            return False                      # 다른 사이트가 보낸 요청
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        return site in ("", "same-origin", "none")

    def token_ok(self, query):
        got = (query.get("t") or [""])[0] or (self.headers.get("X-Token") or "")
        return secrets.compare_digest(got, TOKEN)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > MAX_BODY:
            self.close_connection = True
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---------- routes ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if not self.origin_ok() or not self.token_ok(query):
            self.send_error(403, "forbidden - open the URL the program printed")
            return

        if route in ("/", "/index.html"):
            try:
                with open(UI_FILE, "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(500, "ui.html not found")
                return
            self.send_bytes(body, "text/html; charset=utf-8", cache=False)
            return

        if route == "/api/browse":
            path = pick_folder_dialog()
            if path is None:
                self.send_json({"ok": False, "reason": "cancelled"})
            else:
                self.send_json({"ok": True, "path": os.path.normpath(path)})
            return

        if route == "/file":
            rel = query.get("p", [""])[0]
            full = resolve_under_root(rel)
            if not full or not os.path.isfile(full):
                self.send_error(404, "not found")
                return
            if os.path.splitext(full)[1].lower() not in IMAGE_EXTS:
                # 폴더에 섞여 있는 문서·스크립트까지 내보내지 않는다
                self.send_error(403, "not an image")
                return
            ctype, _ = mimetypes.guess_type(full)
            try:
                size = os.path.getsize(full)
                f = open(full, "rb")
            except OSError:
                self.send_error(404, "unreadable")
                return
            with f:  # 통째로 읽지 않고 흘려보낸다. 수 GB짜리 파일도 메모리를 안 먹는다
                self.send_response(200)
                self.send_header("Content-Type", ctype or "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "max-age=3600")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                shutil.copyfileobj(f, self.wfile)
            return

        self.send_error(404, "not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if not self.origin_ok() or not self.token_ok(urllib.parse.parse_qs(parsed.query)):
            self.send_error(403, "forbidden")
            return
        data = self.read_json()
        if data is None:
            self.send_error(413, "body too large")
            return

        if route == "/api/scan":
            raw = (data.get("path") or "").strip().strip('"').strip("'")
            recursive = bool(data.get("recursive"))
            if not raw:
                self.send_json({"ok": False, "error": "폴더 경로를 입력해 주세요."})
                return
            root = os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
            if not os.path.isdir(root):
                self.send_json({"ok": False, "error": "폴더를 찾을 수 없습니다: %s" % root})
                return
            try:
                items, truncated = scan_folder(root, recursive)
            except OSError as exc:
                self.send_json({"ok": False, "error": "폴더를 읽을 수 없습니다: %s" % exc})
                return
            with STATE_LOCK:
                STATE["root"] = root
            self.send_json({
                "ok": True,
                "root": root,
                "count": len(items),
                "truncated": truncated,
                "max": MAX_FILES,
                "items": items,
            })
            return

        if route == "/api/export":
            paths = data.get("paths") or []
            with STATE_LOCK:
                root = STATE["root"]
            if not root:
                self.send_json({"ok": False, "error": "폴더가 지정되지 않았습니다."})
                return
            if not paths:
                self.send_json({"ok": False, "error": "즐겨찾기에 담긴 이미지가 없습니다."})
                return
            dest_dir = os.path.join(root, "Selected")
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as exc:
                self.send_json({"ok": False, "error": "Selected 폴더 생성 실패: %s" % exc})
                return

            copied, failed = 0, []
            for rel in paths:
                src = resolve_under_root(rel)
                if not src or not os.path.isfile(src):
                    failed.append(rel)
                    continue
                base = os.path.basename(src)
                stem, ext = os.path.splitext(base)
                target = os.path.join(dest_dir, base)
                n = 1
                while os.path.exists(target):
                    if norm(target) == norm(src):
                        break
                    target = os.path.join(dest_dir, "%s_%d%s" % (stem, n, ext))
                    n += 1
                try:
                    if norm(target) != norm(src):
                        shutil.copy2(src, target)
                    copied += 1
                except OSError:
                    failed.append(rel)
            self.send_json({
                "ok": True,
                "copied": copied,
                "failed": failed,
                "dest": dest_dir,
            })
            return

        self.send_error(404, "not found")


class ThreadingServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def find_port(start=8777, tries=40):
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("사용 가능한 포트를 찾지 못했습니다.")


def setup_console():
    """Windows 콘솔에서 한글이 깨지지 않도록 출력 인코딩을 UTF-8로 맞춘다."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    setup_console()
    if not os.path.isfile(UI_FILE):
        print("ui.html 을 찾을 수 없습니다:", UI_FILE)
        input("엔터를 누르면 종료합니다...")
        sys.exit(1)
    try:
        port = find_port()
    except RuntimeError as exc:
        print(exc)
        input("엔터를 누르면 종료합니다...")
        sys.exit(1)
    for host in ("127.0.0.1:%d" % port, "localhost:%d" % port):
        ALLOWED_HOSTS.add(host)
        ALLOWED_ORIGINS.add("http://" + host)
    url = "http://127.0.0.1:%d/?t=%s" % (port, TOKEN)
    httpd = ThreadingServer(("127.0.0.1", port), Handler)
    print("=" * 52)
    print("  Image Selector")
    print("  브라우저에서 열림 →", url)
    print("  이 창을 닫거나 Ctrl+C 를 누르면 종료됩니다.")
    print("=" * 52, flush=True)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
