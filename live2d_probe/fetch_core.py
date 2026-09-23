"""
fetch_core.py — 抓取 Cubism Core 运行时并缓存到本地

Live2D 的专有许可不允许再分发 Core，所以它不能进仓库、不能随包发。
但也不必让用户自己去找文件 —— 官方 CDN 是公开可取的。

关键：**裸请求会返回 403**，必须带浏览器 User-Agent 才是 200。
这一点实测过（不带 UA → 403 Forbidden；带 UA → 200，207155 字节，ACAO: *）。

正式集成时把 ensure_core() 抄进 standalone.py 的启动流程即可。
"""

import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE_PATH = HERE / "vendor" / "live2dcubismcore.min.js"

CORE_URL = "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def ensure_core(path: Path = CORE_PATH, timeout: int = 30) -> bool:
    """本地已有就直接用；否则从官方 CDN 取一份缓存下来。"""
    if path.is_file() and path.stat().st_size > 50_000:
        return True

    path.parent.mkdir(parents=True, exist_ok=True)

    # 先探测本地是否已有可用的（用户可能自己放过一份）
    for candidate in (
        Path.home() / ".dsh" / "pets" / ".runtime" / "live2dcubismcore.min.js",
        Path.home() / ".dsh" / "pets" / "live2dcubismcore.min.js",
    ):
        if candidate.is_file() and candidate.stat().st_size > 50_000:
            path.write_bytes(candidate.read_bytes())
            print(f"[core] 复用本地副本: {candidate}")
            return True

    try:
        req = urllib.request.Request(CORE_URL, headers={
            "User-Agent": UA,          # <- 少了这个就是 403
            "Referer": "https://www.live2d.com/",
            "Accept": "*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                print(f"[core] CDN 返回 {resp.status}")
                return False
            blob = resp.read()
    except Exception as exc:
        print(f"[core] 下载失败: {exc!r}")
        return False

    if b"Live2DCubismCore" not in blob:
        print("[core] 内容里没有 Live2DCubismCore 符号，疑似被拦截")
        return False

    path.write_bytes(blob)
    print(f"[core] 已缓存 {len(blob)} 字节 -> {path}")
    return True


if __name__ == "__main__":
    ok = ensure_core()
    sys.exit(0 if ok else 1)
