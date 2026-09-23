"""
serve.py — Live2D pipeline 探针服务器

验证目标：Python 起一个 HTTP 服务，浏览器从同源加载
  Cubism Core (专有运行时) + vendor 渲染引擎 (MIT) + 模型文件树
能否真正把 DS鲸鱼娘 渲染出来。

设计要点
--------
* 页面通过 POST /report 把渲染结果（含像素采样与截图）回报给服务器 ——
  这样验证完全不依赖 CDP / playwright 之类的自动化库。
* 启动时做一次「模型引用闭包预检」：解析 model3.json，确认它引用的每一个
  文件都实际存在。dsh 仓库的踩坑记录里有一整节是「model3.json 指向中文
  文件名，44 个表情全部 404」，预检就是为了在小窗口里先抓出这类问题。
"""

import json
import os
import sys
import time
import threading
import posixpath
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "model" / "ds-whale-girl"
MODEL_JSON = MODEL_DIR / "c_0120.model3.json"
VENDOR_DIR = HERE / "vendor"
HITLOG = HERE / "hits.log"
# 截图文件名可覆盖：Chrome 那次与 WebView2 那次各存一份，
# 否则后跑的那次会把前一次的截图吃掉（这个坑踩过两次）。
SHOT_NAME = os.environ.get("PROBE_SHOT_NAME", "shot.png")


def record_hit(method: str, path: str, status: int = 0):
    """把每个请求落盘。WebView2 里页面"没反应"时，先看它到底请求了什么。"""
    try:
        with HITLOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {method} {path} -> {status}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 预检：模型引用闭包
# ---------------------------------------------------------------------------

def collect_closure(entry_file: Path):
    """返回 model3.json 递归引用的所有相对路径（相对模型根目录）。"""
    root = entry_file.parent
    data = json.loads(entry_file.read_text(encoding="utf-8"))
    refs = data.get("FileReferences", {})
    found = []

    if refs.get("Moc"):
        found.append(refs["Moc"])
    for tex in refs.get("Textures", []) or []:
        found.append(tex)
    for key in ("Physics", "Pose", "DisplayInfo", "UserData"):
        if refs.get(key):
            found.append(refs[key])

    for _group, entries in (refs.get("Motions") or {}).items():
        for item in entries or []:
            if item.get("File"):
                found.append(item["File"])
            if item.get("Sound"):
                found.append(item["Sound"])

    for item in refs.get("Expressions", []) or []:
        if item.get("File"):
            found.append(item["File"])

    return data, root, sorted(set(found))


def preflight():
    """检查闭包完整性 + 顺带统计动作时长（宿主侧定时收尾要用）。"""
    lines = []
    if not MODEL_JSON.is_file():
        return False, [f"FATAL: model3.json not found at {MODEL_JSON}"]

    data, root, refs = collect_closure(MODEL_JSON)
    missing = []
    for rel in refs:
        if not (root / rel).is_file():
            missing.append(rel)

    lines.append(f"model3.json Version={data.get('Version')}")
    lines.append(f"closure: {len(refs)} referenced files, {len(refs) - len(missing)} present, {len(missing)} MISSING")
    if missing:
        for m in missing:
            lines.append(f"  MISSING -> {m}")

    motions = (data.get("FileReferences", {}).get("Motions") or {})
    lines.append(f"motion groups ({len(motions)}): {', '.join(motions.keys())}")

    loops, durations = [], []
    for group, entries in motions.items():
        for item in entries or []:
            f = root / item["File"]
            if not f.is_file():
                continue
            mj = json.loads(f.read_text(encoding="utf-8"))
            meta = mj.get("Meta", {})
            d, lp = meta.get("Duration"), meta.get("Loop")
            loops.append(lp)
            durations.append(d)
            lines.append(f"  {group:<14} duration={d:<8} loop={lp}")

    if loops and all(lp is True for lp in loops):
        lines.append("WARN: every motion declares \"Loop\": true -> motionFinish never fires;")
        lines.append("      the host must time each action out from Duration above.")

    expressions = (data.get("FileReferences", {}).get("Expressions") or [])
    lines.append(f"expressions: {len(expressions)}")

    groups = data.get("Groups") or []
    lines.append(f"cubism groups: {', '.join(g.get('Name', '?') for g in groups) or '(none)'}")
    if not any(g.get("Name") == "HitAreas" for g in groups):
        lines.append("NOTE: no HitAreas declared -> click testing needs an alpha-silhouette mask,")
        lines.append("      the engine's hitTest() will not work on this model.")

    ok = not missing
    return ok, lines


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _safe_join(base: Path, url_path: str):
    """把 URL 路径安全地映射到 base 之下，越界返回 None。"""
    rel = urllib.parse.unquote(url_path)
    rel = posixpath.normpath("/" + rel.lstrip("/")).lstrip("/")
    if rel in ("", "."):
        return None
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target


class ProbeHandler(SimpleHTTPRequestHandler):
    server_version = "Live2DProbe/1.0"

    def log_message(self, fmt, *args):
        if self.path == "/report":
            return
        sys.stderr.write("[probe] %s\n" % (fmt % args))

    # -- GET ---------------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/", "/probe.html", "/index.html"):
            return self._file(HERE / "probe.html", "text/html; charset=utf-8")

        # 同目录下的其它探针页（smoke.html 等）
        if path.endswith(".html"):
            target = _safe_join(HERE, path.lstrip("/"))
            if target is not None and target.is_file():
                return self._file(target, "text/html; charset=utf-8")

        for prefix, base in (("/vendor/", VENDOR_DIR), ("/model/", HERE / "model")):
            if path.startswith(prefix):
                target = _safe_join(base, path[len(prefix):])
                if target is None:
                    return self._plain(403, "forbidden path")
                if not target.is_file():
                    return self._plain(404, "not found: " + path)
                return self._file(target, _guess_type(target.suffix))

        return self._plain(404, "not found: " + path)

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/report":
            return self._plain(404, "not found")

        record_hit("POST", self.path, 0)  # 0 = 已到达、尚未处理完
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")

        try:
            payload = json.loads(raw)
        except Exception as exc:
            return self._plain(400, "bad json: %r" % (exc,))

        # 增量追踪记录（带 seq）：逐条追加，不当成最终报告
        if isinstance(payload, dict) and "seq" in payload:
            with (HERE / "trace.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            print(f"[probe] trace #{payload.get('seq')} {payload.get('step')}", flush=True)
            return self._plain(200, "traced")

        # 截图单独落盘，避免塞进 JSON
        shot = payload.pop("finalShot", None)
        (HERE / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if isinstance(shot, str) and shot.startswith("data:image/png;base64,"):
            import base64
            try:
                blob = base64.b64decode(shot.split(",", 1)[1])
                (HERE / "shot.png").write_bytes(blob)
                payload["_shotBytes"] = len(blob)
            except Exception as exc:
                payload["_shotError"] = repr(exc)

        ok = bool(payload.get("ok"))
        print("\n[probe] ===== REPORT RECEIVED =====")
        print(f"[probe] ok={ok} stage={payload.get('stage')} elapsed={payload.get('elapsedMs')}ms")
        for err in payload.get("errors", []) or []:
            print(f"[probe] ERROR {err}")
        px = payload.get("pixels") or {}
        if px:
            print(f"[probe] pixels opaque={px.get('opaque')} semi={px.get('semi')} "
                  f"ratio={px.get('ratio')}")
        gl = payload.get("gl") or {}
        if gl:
            print(f"[probe] GL={gl.get('version')} renderer={gl.get('renderer')}")
        if (HERE / SHOT_NAME).is_file():
            print(f"[probe] screenshot -> {HERE / SHOT_NAME}")
        print("[probe] ===========================\n", flush=True)

        (HERE / ".done").write_text("1", encoding="utf-8")
        return self._plain(200, "ok")

    # -- helpers -----------------------------------------------------------
    def _file(self, path: Path, ctype: str):
        if not path.is_file():
            record_hit(self.command, self.path, 404)
            return self._plain(404, "not found")
        blob = path.read_bytes()
        record_hit(self.command, self.path, 200)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        # 显式允许同源下的 fetch/wasm，说明这不是"靠 CORS 侥幸"
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        try:
            self.wfile.write(blob)
        except Exception:
            pass

    def _plain(self, code: int, msg: str):
        record_hit(self.command, self.path, code)
        blob = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        try:
            self.wfile.write(blob)
        except Exception:
            pass


def _guess_type(suffix: str) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".moc3": "application/octet-stream",
        ".css": "text/css; charset=utf-8",
    }.get(suffix.lower(), "application/octet-stream")


def serve(port: int):
    srv = ThreadingHTTPServer(("127.0.0.1", port), ProbeHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    port = int(os.environ.get("PROBE_PORT", "19877"))
    ok, lines = preflight()
    print("=== preflight: model reference closure ===")
    for ln in lines:
        print("  " + ln)
    print("  => " + ("OK" if ok else "INCOMPLETE"))
    print()

    # 只清自己这一次的产物；尤其别用硬编码的 shot.png，
    # 否则 `1_run_browser.bat` 一起服就把 Chrome 那次的截图删掉了。
    for stale in (".done", "report.json", SHOT_NAME):
        p = HERE / stale
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    serve(port)
    print(f"[probe] serving on http://127.0.0.1:{port}/  (Ctrl+C to stop)", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n[probe] bye")
