"""
live2d_assets.py — Live2D 素材定位、校验与 Cubism Core 获取

被 common.py / standalone.py / main.py 引用。

素材布局（相对本文件）
--------------------
    assets/live2d/vendor/live2d-vendor.js          MIT（pixi.js + 引擎，可自由分发）
    assets/live2d/vendor/live2dcubismcore.min.js   Live2D 专有运行时，**不可再分发**
    assets/live2d/model/ds-whale-girl/...          CC BY-NC-SA 4.0（署名·非商用·同协议）

两个关键约束（都在 live2d_probe/ 里实测验证过）
---------------------------------------------
1. Cubism Core 不能进仓库（专有许可）。但也不用让用户自己去找：
   官方 CDN 公开可取。**注意裸请求返回 403，必须带浏览器 UA 才是 200** ——
   而且网关策略会变，所以拿到后必须**校验内容**（长度 + 含 Live2DCubismCore 符号），
   别把错误页当成功存下来。
2. 模型引用闭包必须完整。dsh 的作者仓库踩过「model3.json 指向的文件名对不上，
   44 个表情全部 404」的坑，所以这里启动时做一次闭包预检。

禁用开关
-------
    AIRI_LIVE2D=0        强制退回图片 / CSS 角色（做 A/B 对照用）
    AIRI_CUBISM_CORE     指定已有的 core 文件路径，跳过下载
"""

import json
import os
import urllib.request
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent

ASSETS_DIR = _SCRIPT_DIR / 'assets' / 'live2d'
VENDOR_DIR = ASSETS_DIR / 'vendor'
MODEL_ROOT = ASSETS_DIR / 'model'
MODEL_ENTRY = MODEL_ROOT / 'ds-whale-girl' / 'c_0120.model3.json'

VENDOR_JS = VENDOR_DIR / 'live2d-vendor.js'
CORE_PATH = VENDOR_DIR / 'live2dcubismcore.min.js'

CORE_URL = 'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js'
CORE_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

MIN_CORE_BYTES = 50_000
MIN_VENDOR_BYTES = 100_000


def enabled() -> bool:
    """AIRI_LIVE2D=0 时强制关闭（退回原来的图片/CSS 角色）。"""
    return os.environ.get('AIRI_LIVE2D', '1').strip().lower() not in ('0', 'false', 'no')


def silhouette_enabled() -> bool:
    """异形窗口开关，AIRI_SILHOUETTE=0 关闭。

    ⚠️ 这个开关**只管鼠标点穿，不管画面透明**（第三轮实测修正）：
    画面透明靠的是关掉 DWM 的 Mica 背景材质（见 common.disable_window_backdrop），
    SetWindowRgn 裁的是宿主 Form 的 GDI 表面，而 WebView2 走 DirectComposition
    合成，父窗口的 region 裁不到它 —— 所以 region 设上之后画面纹丝不动
    （实测：SetWindowRgn 返回成功、GetWindowRgn 读回逐值正确，屏幕像素零变化）。
    它真正的作用是让轮廓外面的鼠标事件落到别的窗口上。

    出问题时关掉它：窗口变回整块矩形（画面透明不受影响，只是鼠标不再点穿，
    那块空白区域会挡住底下的窗口）。
    """
    return os.environ.get('AIRI_SILHOUETTE', '1').strip().lower() not in ('0', 'false', 'no')


CARD_MODES = ('off', 'tight', 'square', 'frame', 'all')


def card_mode() -> str:
    """角色底板模式，AIRI_CARD 覆盖，默认 'square'。

    这个模型自带白色美术（蕾丝女仆头饰、白书桌板、巴菲奶油）。窗口真透明之后
    那些白在深色桌面上会散成好几块碎白，看起来像渲染坏了。底板就是垫在角色
    底下的一张紧贴其**实测外接框**的卡，把碎白并进同一张卡里。

    off    不画底板（回到裸角色）
    tight  紧贴角色的圆角卡（白面最少）
    square 同上，但取成正方形（默认 —— 用户原话"以最小的白色正方形框住"）
    frame  只描一圈白框，卡内照旧透出桌面（白面积 = 0）
    all    连名牌（Airi / online）一起包进同一张卡

    它只影响观感，和 SILHOUETTE / LIVE2D 都独立。
    """
    v = os.environ.get('AIRI_CARD', '').strip().lower()
    return v if v in CARD_MODES else 'square'


def ensure_core(path: Path = CORE_PATH, timeout: int = 30) -> bool:
    """本地已有就直接用；否则从官方 CDN 取一份缓存到本地。

    失败不抛异常 —— 调用方据此退回图片角色。返回是否可用。
    """
    env_core = os.environ.get('AIRI_CUBISM_CORE', '').strip()
    if env_core and Path(env_core).is_file() and Path(env_core).stat().st_size >= MIN_CORE_BYTES:
        return True

    if _core_ok(path):
        return True

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f'[airi-live2d] cannot create {path.parent}: {exc!r}')
        return False

    # 复用 dsh 桌宠装过的副本，省一次下载
    for candidate in (
        Path.home() / '.dsh' / 'pets' / '.runtime' / 'live2dcubismcore.min.js',
        Path.home() / '.dsh' / 'pets' / 'live2dcubismcore.min.js',
    ):
        if _core_ok(candidate):
            try:
                path.write_bytes(candidate.read_bytes())
                print(f'[airi-live2d] cubism core reused from {candidate}')
                return True
            except OSError:
                break

    try:
        req = urllib.request.Request(CORE_URL, headers={
            'User-Agent': CORE_UA,      # 少了这个就是 403
            'Referer': 'https://www.live2d.com/',
            'Accept': '*/*',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                print(f'[airi-live2d] cubism core CDN returned {resp.status}')
                return False
            blob = resp.read()
    except Exception as exc:
        print(f'[airi-live2d] cubism core download failed: {exc!r}')
        return False

    if b'Live2DCubismCore' not in blob:
        print('[airi-live2d] cubism core content looks wrong (no Live2DCubismCore symbol)')
        return False
    if len(blob) < MIN_CORE_BYTES:
        print(f'[airi-live2d] cubism core too small ({len(blob)} bytes)')
        return False

    try:
        path.write_bytes(blob)
    except OSError as exc:
        print(f'[airi-live2d] cannot cache cubism core: {exc!r}')
        return False
    print(f'[airi-live2d] cubism core cached ({len(blob)} bytes) -> {path}')
    return True


def _core_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_CORE_BYTES
    except OSError:
        return False


def _closure_refs(data: dict):
    """model3.json 引用到的所有相对路径（相对模型根目录）。"""
    refs = data.get('FileReferences') or {}
    out = []
    if refs.get('Moc'):
        out.append(refs['Moc'])
    out.extend(refs.get('Textures') or [])
    for key in ('Physics', 'Pose', 'DisplayInfo', 'UserData'):
        if refs.get(key):
            out.append(refs[key])
    for entries in (refs.get('Motions') or {}).values():
        for item in entries or []:
            if isinstance(item, dict) and item.get('File'):
                out.append(item['File'])
    for item in refs.get('Expressions') or []:
        if isinstance(item, dict) and item.get('File'):
            out.append(item['File'])
    return sorted(set(out))


def check_assets() -> tuple:
    """启动时的素材自检。返回 (是否可用, 说明行列表)。"""
    lines = []

    if not enabled():
        return False, ['AIRI_LIVE2D=0 -> live2d disabled by request']

    if not VENDOR_JS.is_file() or VENDOR_JS.stat().st_size < MIN_VENDOR_BYTES:
        return False, [f'missing or truncated renderer: {VENDOR_JS}']

    if not MODEL_ENTRY.is_file():
        return False, [f'missing model entry: {MODEL_ENTRY}']

    try:
        data = json.loads(MODEL_ENTRY.read_text(encoding='utf-8'))
    except Exception as exc:
        return False, [f'model3.json unreadable: {exc!r}']

    root = MODEL_ENTRY.parent
    refs = _closure_refs(data)
    missing = [r for r in refs if not (root / r).is_file()]
    lines.append(f'model Version={data.get("Version")} closure={len(refs)} files, '
                 f'{len(refs) - len(missing)} present, {len(missing)} missing')
    for m in missing[:8]:
        lines.append(f'  MISSING -> {m}')

    if missing:
        lines.append('model reference closure is incomplete -> fall back to image character')
        return False, lines

    if not ensure_core():
        lines.append('cubism core unavailable -> fall back to image character')
        return False, lines

    lines.append(f'cubism core {CORE_PATH.stat().st_size} bytes')
    return True, lines


if __name__ == '__main__':
    ok, detail = check_assets()
    print('=== Airi live2d assets ===')
    for ln in detail:
        print('  ' + ln)
    print('  => ' + ('READY' if ok else 'NOT AVAILABLE'))
