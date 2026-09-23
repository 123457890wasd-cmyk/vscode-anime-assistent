# live2d_probe — Live2D pipeline 可行性验证

验证「用 DS鲸鱼娘 Live2D 替代 `desktop_pet/assets/character.png`」这条路能否跑通。
**这个目录本身是个独立的验证沙盒**——验证阶段它不碰桌宠本体代码；
集成本身是**另外单独做的**（见下方「集成已完成」）。

> **当前状态：已跑通并集成完毕。** 桌宠现在默认就用 Live2D 渲染角色，
> 素材缺失或任一步失败会自动退回图片 / CSS 角色。

## 结论速览

| 环节 | 结果 |
|---|---|
| 模型引用闭包（57 个文件） | ✅ 全部可解析 |
| Cubism Core + MIT 渲染引擎加载 | ✅ |
| 真实 WebGL 渲染 | ✅ WebGL 2.0 / ANGLE / RTX 5060 / D3D11 |
| 静态帧绘制 | ✅ 21.7% 不透明像素 |
| idle 动作驱动画面 | ✅ 11.75% 像素随帧变化 |
| 表情施加 / 还原 | ✅ |
| 非 idle 动作切换 | ✅ |
| **pywebview + WebView2** | ✅ **已由用户在自己桌面上确认渲染成功** |
| **桌宠本体 `ui.html` 端到端** | ✅ 7/7 断言通过（见下） |
| **窗口真透明（关掉 DWM Mica 背景）** | ✅ 用户真实窗口上**屏幕实测**：0.00% → 90.49% 透出桌面（`live_fix4.txt`）。⚠️ **第五轮修正**：那 90% 里**很大一部分其实是 region 裁剪**（窗口被裁成 DOM 形状，形状之外露桌面）。真正让"页面没画的像素"变透明的是第五轮的 `AIRI_WIN_BG` —— `live_fix5.txt`：与桌面一致 **52.41% → 79.28%** |
| **窗口形状（`SetWindowRgn`）** | ✅ 31/31 断言通过，`GetWindowRgn` 读回逐值相同。⚠️ **第五轮修正**：它**确实会裁画面**（形状之外露桌面），第三轮"画面不受它影响"那句只在"整窗被 Mica 盖住"的前提下成立；它同时负责鼠标点穿 |
| **块状白（气泡/名牌背后那一圈方底）** | ✅ 第五轮定位到窗口层面（region 的 pad 环露宿主 Form 底色 `#F0F0F0`）。⚠️ **第六轮修正**：第五轮的颜色键挖洞（`TransparencyKey`）会把窗口打成 alpha=0 / 让 WebView2 内容整窗鼠标穿透（"看得见点不着"，即用户报的点击/拖拽回归）—— **颜色键路线整体弃用**。第六轮 `dark` + pad 1px 只是把白边压成黑边；**第七轮黑边彻底消灭**：region 同形状（圆角/多边形）+ 页面全部不透明绘制 + `.pet-plate` 名牌板（`live_fix6.txt`、`winbg_fix6.txt`、`colorkey_test2.txt`） |
| **点击说话（alpha 命中 + 台词池）** | ✅ 26/26 断言通过（真 Chrome 合成事件，拖拽不误触发）。第六轮起前端带黑匣子：`onerror`/`unhandledrejection` 上报 + 桥自检 ping，`[airi-js]` 日志可分辨页面崩 / 桥断 / 没命中 |

Chromium 侧 5/5 断言全通过。WebView2 侧在本自动化会话里曾表现随机
（同一份代码，`loaded` 事件有时触发有时不触发）——但那是我这边的**会话环境**问题，
不是管道问题：用户在真实桌面上双击 `2_run_webview2.bat` 跑通了，
窗口里模型正常渲染（`result_A_opaque.txt` + `report.json` 里
`ok:true / stage:done / elapsedMs:623`，`hits.log` 记录了一次干净的完整加载：
core → vendor → model3.json → moc3 → idle → texture_00/01 → physics3 → exp3/motion3）。

> **教训**：`loaded` 事件在无人值守会话里不可靠，**不能当作产品缺陷的证据**。
> 判定 WebView2 可用性必须由人在交互桌面上跑一次。

## 集成已完成 —— 现在桌宠跑的就是 Live2D

上面的验证都是拿**探针页**做的。探针页能渲染 ≠ 桌宠本体能渲染。
`verify_pet_ui.py` 补上了最后一环：它直接消费

```
desktop_pet/common.py : load_html() + start_asset_server()
desktop_pet/ui.html   （含 Live2D 模块 / 情绪映射 / 眨眼驱动）
```

也就是**用户实际会跑到的那条路径**（真 Chrome + 真 WebGL，不是 mock）。

```
status : done (10119 ms)
live2d stage  : visible=True          charImg 隐藏: True   cssChibi 隐藏: True
canvas        : 320x420 opaque=39975 ratio=0.2974
ticker 在跑   : changed=17816 px / 600ms
接缝处 eye 值 : postMin=0 postMax=1 blinkFrames=32 completeCycles=1
                                    [PASS] × 7
```

渲染之外，`smoke_pet_server.py` 单独把**生产静态服务器入口**也测了
（`verify_pet_ui.py` 用的是真处理器但服务器是它自己 new 的，所以
`standalone.py` 实际调用的 `start_asset_server()` **没被覆盖** —— 它一旦绑定失败，
上层就 `sys.exit`，桌宠连窗口都开不出来）：

```
start_asset_server -> 'http://127.0.0.1:9227/' port=9227
闭包 57 个文件全部 200 且有内容           all served
8 条越界路径全被挡（非 200 / 无源码内容）   all blocked
通过 14/14
                                    [PASS] × 14
```

越界用例含 `../`、URL 编码的 `%2e%2e`、`..%2f`、`....//`、反斜杠，
以及 `/vendor/` 一侧的越界 —— 全部被 `_safe_join()` 的 realpath 包含检查挡住。

还有一条**最容易漏**的：用户的真实启动方式是「VS Code 里点状态栏按钮」，
而那是扩展 spawn `standalone.py`，用的是**另一个 Python（Python312）、另一个 cwd**，
`verify_pet_ui.py` / `smoke_pet_server.py` 都覆盖不到。`probe_vscode_launch.py`
照抄 `extension.ts:findPythonPath()` 的优先级与 `spawn()` 参数跑真脚本：

```
extension.ts 会挑的 Python : ...\Python312\python.exe   (AIRI_PYTHON_PATH 未设)
cwd                        : ...\desktop_pet
[airi-live2d] model Version=3 closure=57 files, 57 present, 0 missing
[airi-live2d] cubism core 207155 bytes
[airi-live2d] live2d ENABLED
[airi-standalone] UI served on http://127.0.0.1:7536/
通过 10/10
```

桌宠本体的改动清单（`git status`）：

| 文件 | 改动 |
|---|---|
| `desktop_pet/live2d_assets.py` | **新增**。资源定位、Cubism Core 自动获取与校验、闭包预检（第二轮加 `silhouette_enabled()`） |
| `desktop_pet/assets/live2d/` | **新增**（68 文件 / 5.03 MB）。`vendor/` + `model/ds-whale-girl/` |
| `desktop_pet/common.py` | 废掉 `create_temp_html()`；新增 `_make_handler()` / `start_asset_server()`；**第二轮**加 `WindowAPI.set_click_handler()` / `pet_click()` / `set_window_region()` + `_apply_window_region()`（`ExtCreateRegion` + `SetWindowRgn`） |
| `desktop_pet/standalone.py` | 起静态服务、按 `check_assets()` 结果决定是否注入 Live2D；**第二轮**加 `CLICK_LINES` 台词池 + `_pick_click_line()`，并接上 `set_click_handler` / `set_region_enabled` |
| `desktop_pet/main.py` | 同上（旧的入口，只加了 `silhouette_enabled()` 与 `set_region_enabled`；未接点击词池） |
| `desktop_pet/ui.html` | `#live2dStage` 舞台、情绪→表情映射、眨眼驱动、图片/CSS 降级；**第二轮**加 `characterRects()` / `collectUiRects()` / `pushSilhouette()` 轮廓推送，`alphaAt()` / `zoneAt()` / `handlePetClick()` 点击命中，以及 5px 的拖拽/点击判别 |

跑验证：

```powershell
$venv = "C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe"
& $venv verify_pet_ui.py      # 产物：pet_ui_result.txt / pet_ui_shot.png
```

## 第二轮：窗口镂空 + 点击说话

用户看到跑起来的截图后提了两条需求：**「能否设置成透明界面」** 和
**「点击后会回复对应的内容」**。这两条是同一个根因的两个出口，所以放一起说。

### 「透明界面」的第一次判断（**已被第三轮推翻，保留作记录**）

用户截图里角色坐在一块 `#F0F0F0` 的灰白方块上。先把那块颜色量出来
（`analyze_screenshot.py` 读剪贴板截图，30 px 网格采样）：

```
窗口内部 #F0F0F0  59.59%     <- 就是这块
纯白   #FFFFFF     0.73%
```

`#F0F0F0` 正好是 `SystemColors.Control`，于是当时判断"这块是宿主 Form 的底色"。
对 `winforms.py` 的阅读本身没错（`winforms.py:286-292` 的 `BackColor = ...`
确实只在 `else` 分支里，`transparent=True` 时 Form 的 BackColor 不被赋值）：

> **结论只对了一半。** 那块铺满窗口的方块不是 Form 画的，是 **DWM 的 Mica
> 背景材质**。而且 `#F0F0F0` 这个读数本身有陷阱 —— 浅色模式下 Form 底色
> 也是 `#F0F0F0`，两个来源撞成同一个值，才把方向带偏。
> 真正的根因见下面的「第三轮」。

### 第一版为什么选了 `SetWindowRgn`（当时的选择，保留作记录）

| 路子 | 当时的结论 | 第三轮的复核 |
|---|---|---|
| `Form.BackColor = Color.Transparent` | ❌ 赋值成功，读数仍是 `#F0F0F0` | ✅ 结论仍成立（无父窗体的顶层 Form 没法这样透明） |
| `TransparencyKey` / `LWA_COLORKEY` | ⚠️ 无法定论（抓屏不可信） | ❌ **确定无效**：Mica 不在 GDI 表面里，颜色键挖不到 |
| **`SetWindowRgn`** | ✅ 当时认为"确定可行，作为实现方案" | ⚠️ **它不改变画面**，只影响鼠标命中 |

当时用的判据是 `GetWindowRgn` 读回：

```
region_before / region_after 对照：got=(95,138,189,303)  want=(95,138,189,303)   ✅ 逐值相同
```

> ⚠️ **"API 读回相同" ≠ "屏幕上生效"。** 这是这轮最大的教训 ——
> 第三轮实测：region 读回 `COMPLEXREGION 138x220` 的轮廓，
> 屏幕上一个像素都没裁（`live_fix2.txt` / `live_fix4.txt`）。
> **以后判"透明生效没有"，只认屏幕 BitBlt 的像素，不认 API 返回值。**

当时列的三个"不用 layered"的理由，第 2、3 条现在看仍然没错
（WebView2 在 WinForms 里是 HWND windowed hosting，有 airspace 问题；
`SetWindowRgn` 不动渲染路径）——只是第 3 条把"不动渲染路径"错当成了"能裁画面"。

> 微软官方修 airspace 的方案是 `WebView2CompositionControl`（visual hosting）
> ——pywebview 没用它，也没有暴露这种选项。

---

## 第三轮：真透明 = 关掉 DWM 的 Mica 背景材质（**真正的根因**）

用户换了张截图回来：「并不是透明的，变成了纯黑色，而且角色周围还有块状白色」。

### 现象与证据

1. 那块方块的颜色跟着**系统主题**走：浅色模式 `#F0F0F0`，深色模式 `#202020`
   （用户当前 `AppsUseLightTheme = 0`）。而 `SystemColors.Control` 恰好也是这两个值。
2. 隐藏/显示窗口做差分（`probe_live_alpha.py`，抓屏没有任何 API 代理）：
   ```
   A 窗口可见 : #202020 46.0%  ...       窗口区域 = 抓取区域的 50.84%
   B 窗口隐藏 : #17181A 77.2%  #036054 4.3%   （底下是桌面/视频）
   A vs B 不同像素 = 50.84%   ← 正好等于窗口面积占比 = 窗口里每个像素都是它自己画的
   ```
   → **窗口完全不透明，`#202020` 是自己画的。**
3. 那到底是哪一层画的？在**用户正在跑的窗口**上逐层试（这才是关键一步）：

| 操作 | 结果 |
|---|---|
| `SetWindowRgn`（精确轮廓 + 强推 `SWP_FRAMECHANGED`） | ❌ 画面纹丝不动 |
| `WS_EX_LAYERED` + `LWA_COLORKEY(#202020)` | ❌ 纹丝不动 |
| `WS_EX_LAYERED` + `LWA_ALPHA`（整窗 alpha=0） | ✅ 窗口整个消失（`与桌面一致 = 100%`） |
| **`DwmSetWindowAttribute(hwnd, 38, DWMSBT_NONE)`** | ✅ **`与桌面一致 = 90.49%`** |

只有"整窗 alpha"有效，说明那块颜色**不在窗口的 GDI 重定向表面里** ——
它由 DWM 在合成阶段绘制。那就不是 Form 的锅。

### 根因：pywebview 在深色模式下给窗口装了 Mica

`webview/platforms/winforms.py:333`：

```python
def update_title_bar_theme(self):
    if self.is_dark_theme():                       # 读 AppsUseLightTheme
        DwmSetWindowAttribute(self.Handle.ToInt32(), 20, 1)
        DwmSetWindowAttribute(self.Handle.ToInt32(), 38, 2)   # 38 = DWMWA_SYSTEMBACKDROP_TYPE
    else:                                                      # 2 = DWMSBT_MAINWINDOW = Mica
        DwmSetWindowAttribute(self.Handle.ToInt32(), 20, 0)
        DwmSetWindowAttribute(self.Handle.ToInt32(), 38, 1)    # 1 = DWMSBT_NONE
```

**浅色模式不装 Mica，深色模式装。** 用户从浅色切到深色（或本来就深色），
于是那块方块从 `#F0F0F0` 变成了 `#202020` —— **两张截图的差异一个机制解释完。**

Mica 是 DWM 画的窗口背景材质，不在窗口自己的表面里，所以：

- `SetWindowRgn` 裁不到它（**region 的成功返回值是真的，只是对它无效**）；
- `LWA_COLORKEY` 挖不掉它（颜色键作用在 GDI 表面上）；
- 只有作用在最终合成结果上的 `LWA_ALPHA` 才动得了它。

### 修复

`common.py` 新增 `disable_window_backdrop()`：

```python
DwmSetWindowAttribute(hwnd, 38, DWMSBT_NONE)      # 关掉 Mica
# 并且把 form.update_title_bar_theme 包一层 —— 切主题会把它装回来
```

为什么必须包一层：切系统主题会触发
`SystemEvents.UserPreferenceChanged -> update_title_bar_theme()`，
深色模式下 Mica 会被**重新装回去**，窗口立刻又变回实心方块。

调用点在 `standalone.py` / `main.py`：`webview.start(_after_window_ready)`。
Form 是 `webview.start()` 内部才创建的，所以那里轮询等它出现（最多 20s），
拿不到只告警、不抛异常。

启动日志会留一行（`.airi-pet.log`，实测）：

```
[airi-dwm] Mica backdrop disabled (hwnd=0x341194 backdrop=2->1 set=True guarded=True)
```

`backdrop=2->1` 就是铁证：pywebview 确实装上了 Mica（2），我们确实把它关了（1）。

### 修复后两件事分清楚了

| 需求 | 靠什么 |
|---|---|
| **画面透出桌面** | 关掉 Mica + WebView2 的 `DefaultBackgroundColor=Transparent` + 页面 `background: transparent` |
| **鼠标点穿** | `SetWindowRgn` 裁窗口形状 —— 它**不改画面**，只让形状外的点击落到下层窗口 |

点穿这一条也实测过（`live_fix3.txt`，`WindowFromPoint`）：

```
客户区 (20,20)    -> root=0x000504F8 OTHER   ← 形状之外，命中的是别的窗口
客户区 (140,220)  -> root=0x000E0862 same    ← 形状之内，命中桌宠自己
```

### 「角色周围那块块状白色」不是 bug —— 但第四轮给它做了底板

量下来是两块矩形（80×32 和 19×21），而且**第一轮那张 `#F0F0F0` 截图里就存在**，
只是白压在浅灰上看不出来。用真 Chrome 单独渲染模型（`pet_ui_shot.png`）一看就明白：

**是模型自己的美术** —— 白色蕾丝女仆头饰 + 一张白色书桌板，板上有圣代、笔、回形针。
深色方块一衬，白的地方就"跳"出来了，看着像渲染坏了。

第四轮用户仍然觉得它碍眼，所以要的不是"解释"，是**处理**。处理办法见下面
「第四轮」一节：给角色垫一张紧贴实测外接框的白卡，碎白并进同一张卡里。
"不是 bug"和"该不该处理"是两件事。


### 实现：页面算轮廓，Python 裁窗口

形状由谁决定？**页面。**

```
ui.html                                   common.py
  characterRects()                          set_window_region(payload)
    gl.readPixels(0,0,cw,ch)                  ExtCreateRegion(RGNDATA)
    → 逐行扫描线切成矩形                        SetWindowRgn(hwnd, hrgn, True)
  collectUiRects()  气泡 / 名字 / 状态条
  pushSilhouette()  每 500ms 推一次  ──────▶  _apply_window_region()
```

几个必须踩对的细节：

- **`readPixels` 读的是当前绑定的 framebuffer。** 必须先
  `gl.bindFramebuffer(gl.FRAMEBUFFER, null)` 再读，读完整回去，否则读到的可能是别的 FBO。
- **WebGL 画布原点在左下，DOM 在左上** → 读像素的行号要翻转：
  `row = Math.round((1 - (clientY-rect.top)/rect.height) * ch)`。
- **`RGNDATAHEADER` 恰好 32 字节**：`dwSize=32, iType=1, nCount, nRgnSize=nCount*16,
  rcBound(16B)`，之后接 `nCount × RECT(16B)`。**`SetWindowRgn` 成功后区域归系统所有，
  千万别 `DeleteObject`。**
- **DPI**：client rect 是物理像素、页面 viewport 是逻辑像素，得按 `cw/vw` 缩放
  （实测 284/300 = 0.9467，125% 缩放）。
- **`pushSilhouette()` 必须等 `live2d.ready`。** 否则首次推送只有气泡矩形、
  等于把角色整个裁掉，窗口会"消失"。
- **安全阀**：空矩形 → 拒绝（`empty rects (refused)`）；全部被裁光 → 拒绝
  （`all rects clipped away`）。宁可留个灰框，也不能让窗口变成零面积。

### 实现：点击说话

模型**没有声明 Cubism HitAreas**（`live2d_assets` 预检已报出），
引擎的 `hitTest()` 用不了 → 只能从渲染结果的 alpha 通道反查：

```
mousedown 记 downX/downY
mousemove 超过 5px 就算拖拽，不再算点击     <- 拖窗口和点角色共用同一个 mousedown
mouseup   dragging && !moved 才走 handlePetClick()
            alphaAt(x,y) < 24  → 直接 return（点到空白处不说话）
            zoneAt(clientY)    →  ry<0.45 'head' / <0.85 'body' / else 'desk'
            api.pet_click(zone) → 宿主从台词池随机挑一条 (台词, 情绪)
                               → showBubble(text) + setEmotion(emotion)
```

台词池在 `standalone.py:CLICK_LINES`，每个部位一池，**只用前端认识的四个情绪**
（`surprised`/`angry`/`happy`/`greeting`）——写个前端没有的情绪词，表情会静默不变。
防抖 600 ms，避免狂点刷屏。

### 验证证据

两个脚本都是**真 Chrome + 真 WebGL + 真合成鼠标事件**，不是 mock：

```
verify_click_region.py  31/31 PASS
  ├ 台词池形状 / 情绪词表与 ui.html 的 EMOTION_EXPRESSION 逐词对齐（正则互查）
  ├ pet_click 端到端（含未知 zone 兜底、未装 handler 时安全返回）
  ├ load_html 注入 / 前端各函数存在性
  └ 真窗口区域测试：before/after GetWindowRgn 读数比对 + region_*.png 实证截图

verify_silhouette_js.py 19/19 PASS   (5116 ms)
  视口 500x385
  轮廓矩形: 55 个  面积占比 0.0526  越界 0
  轮廓纵向: top=153 bottom=277
  alphaAt: 角色中心=255  角色左上角=0  页面(1,1)=0
  名字/状态矩形: 2 个
  set_window_region 被调用: 6 次（最后一次带 60 个矩形 = 角色 + 气泡 + 名字）
  点击结果: zones=['body'] 气泡数=1 气泡文本='HARNESS-REPLY-OK' 表情=happy
  拖拽测试: zones 1 -> 1     <- 拖拽确实没有误触发点击
```

`verify_silhouette_js.py` 的做法值得记一下：它在 `</body>` 前注入一段 `HARNESS`，
把 `window.pywebview.api` 换成假对象，捕获 `pet_click` / `set_window_region`
的调用，再用 `dispatchEvent(new MouseEvent(...))` 造合成点击与拖拽。
**这样前端交互逻辑可以脱离 WebView2 验证** —— 在 WebView2 不可用的会话里这是唯一出路。

开关：`AIRI_SILHOUETTE=0` 关掉**窗口形状裁剪**（也就是放弃鼠标点穿；
整块矩形的点击都归桌宠）。**它不影响画面透明** —— 透明靠关 Mica，见第三轮。
与 `live2d_assets.enabled()` 相互独立，所以图片 / CSS 角色也吃这个开关。

### ⚠️ 本次会话**没能**验证的那一条（讲清楚边界）

本自动化会话里 **WebView2 根本不渲染**（基线探针窗口 `PrintWindow`
读回 **0/2500 个非黑像素** = 什么都没画）。所以：

> ✅ **这一条第三轮已经实测掉了。** 结论是：**父窗口的 `SetWindowRgn` 裁不到
> WebView2 的内容**（WebView2 走 DirectComposition 合成，不在父窗口的 GDI 表面里）。
> 但好消息是 —— **画面透明根本不需要 region**，靠的是关掉 DWM Mica +
> WebView2 自己的 `DefaultBackgroundColor=Transparent`；region 只负责鼠标点穿。
> 详见「第三轮：真透明 = 关掉 DWM 的 Mica 背景材质」。
>
> 用户原始诉求（"角色之外透出桌面"）现在**已经在用户自己的真实窗口上
> 以屏幕像素证实**（0.00% → 90.49%）。

`AIRI_SILHOUETTE=0` 现在的作用是：关掉 region，也就是**放弃鼠标点穿**
（点击整块矩形都归桌宠），画面透明不受影响。

## 第四轮：把"块状白"拢进一张底板（`AIRI_CARD`）

用户回帖：**「'角色周围块状白色'这个问题还存在，同时角色上方的对话框也有这种情况。
如果是这样的话修复对话框的问题，同时把角色以最小的白色正方形框住，不要让角色周围
块状白色过于明显」**。

第三轮的结论（"那是模型美术，不是 bug"）**回答的是"这是什么"，没有回答"怎么办"**。
这一轮处理观感。

### 先把"到底谁画了那块白"量清楚

不看图猜，直接读用户截图（`clipboard-...05c95d55.png`）的像素
（`analyze_blocks.py` 找窗口 → 分类 → 连通块）：

```
窗口（含 slab）bbox : (80,46)-(364,487)  = 284x441      ← 与日志里的 client 完全一致
色       面积 bbox(窗口相对)            矩形度 平均色
W     1420 (  91, 297, 171, 328)       0.573 rgb(253,253,253)   ← 白书桌板（模型美术）
W     1404 ( 120, 386, 164, 426)       0.798 rgb(239,229,233)   ← 名牌背后那块（不是美术！）
W     1395 ( 246, 108, 284, 157) [贴边] 0.749 rgb(253,250,250)   ← 隔壁另一只桌宠的白猫压过来
W      321 ( 125, 258, 155, 280)       0.486 rgb(253,243,236)   ← 巴菲上的奶油（美术）
```

**矩形度 0.798 那一块是关键**：位置正好盖住 `.pet-name` + `.pet-status` 两行，
比课本上任何美术元素都"方"，说明是 DOM 画的。于是写 `sample_region.py` 做行程编码：

> 上表那一列的 `rgb(239,229,233)` 是**连通块的平均色**（`analyze_blocks.py` 算的），
> 下面这个 `rgb(241,228,233)` 是**内部采样点的色**（`sample_region.py` 读的）。
> 两个数不一样不是矛盾：这块底自上而下有一丝极淡的渐变，平均下来会被拉低两个灰阶。

- 上下边 y=386 / y=425（高 40），左右边 x=120 / x=163（宽 44），四角有圆角
- 填充 `rgb(241,228,233)`，从上到下还有一丝极淡的渐变
- 里面 Pink 文字 `rgb(255,107,157)` = `--accent` ✓、紫字 `rgb(193,135,250)` ≈ `#c084fc` ✓、
  绿点 `rgb(74,222,128)` = `#4ade80` ✓、`online` 灰字 `rgb(164,161,188)` ≈ `--muted` ✓

文字渲染都对，**只有那块底是多余的**。

### 但它复现不出来 —— 必须讲清楚

我搭了 `probe_ui_blocks.py`（真 Chrome，逐条 CSS 做 A/B：去掉 `.pet-name` 的 `filter`、
去掉 `.bubble` 的 `backdrop-filter`、不用 `background-clip:text`、两两组合），
结果**所有变体都不画那块底** —— 也就是说 headless Chrome 里根本复现不出这个现象。

> ⚠️ 所以这一条属于**推定**，不是实测：方块是 WebView2 独有的渲染行为。

按机制推，能造成"透明底上文字连底一起被栅格化成不透明"的只有三类写法，
全都在这轮里去掉 / 关掉了：

| 写法 | 为什么要动它 |
|---|---|
| `.bubble` / `.context-menu` 的 `backdrop-filter` | 透明窗口上它找不到"背后"可模糊的东西，Chromium 会退化成给这层糊一块不透明的底 —— **对话框那块白最可能就是它** |
| `.pet-name` 的 `filter: drop-shadow()` | `filter` 会把元素提升成独立合成层，透明底上这一层的底可能没被正确清成透明 |
| 全局 `-webkit-font-smoothing: antialiased` | 次像素（ClearType）渲染要求底不透明；页面底是透明的时候，文字的栅格化缓冲区会被按不透明处理 |

### 底板的做法：跟着**实测外接框**走，采样后锁定

"最小"就体现在这里 —— 卡不是写死 `.character-box` 的 160×210，而是用轮廓那套
`readAlphaGrid` 读出来的角色真实外接框：

```
实测（probe_ui_preview.py，视口 284x441）
  canvas 的 CSS 盒 = [62,169,222,379]        = 160x210（.character-box，比角色大一圈）
  角色真实外接框   = [85,209,222,333]        = 137x124
  底板 tight       = [75,199,232,343]        = 157x144（+10px 内边距）
  底板 square      = [75,193,232,350]        = 157x157
```

两个必须踩对的点：

- **必须先取并集再锁定。** 模型一直在做 idle 动作（呼吸/微摆），外接框每次都差几像素；
  卡跟着变就是每 500ms 抖一下，比白边本身难看得多。做法是前 6 次（约 3 秒）取并集
  ——正好把摆动范围整个包住——然后 `cardSamples >= CARD_SAMPLES` 直接焊死。
- **底板要并进窗口形状**（`collectUiRects()` 里 `pairs.push([charCard, 0])`）。
  它是一张实心卡，不并进去就会被 region 裁掉，表现成"卡只显示一部分"。
- 底板用 `position: fixed; z-index: 0`，落在 canvas（`z-index: 1`）之下，
  并且**只在 Live2D 就绪后**才加 `body.card-on` —— 图片兜底角色时 z-index:0 会盖住
  `#charImg`，反而更糟。

### 顺手改的结构：`SILHOUETTE` 不再把底板一起关掉

原来 `pushSilhouette()` 第一行是 `if (!SILHOUETTE || !live2d.ready) return;`。
底板要的是"角色在哪儿"，和"要不要异形窗口"是两件事，所以拆成：

```js
if (!live2d.ready) return;
var ch = characterRects();
if (ch.source !== 'unreliable' && ch.rects.length) updateCharCard(ch.rects);
if (!SILHOUETTE) return;          // ← 只挡窗口形状，不挡底板
```

`verify_click_region.py` 里查这一行的断言也跟着改了 —— 旧断言查的是一行
**故意的、不该再存在**的代码。

### 五种底板（`AIRI_CARD`）

| 值 | 样子 | 不透明像素占窗 |
|---|---|---|
| `off` | 不画底板（回到裸角色） | 17.50% |
| `tight` | 紧贴角色的圆角白卡（白面最少） | 27.68% |
| `square` | 同上但取正方形 —— **默认**（用户原话"以最小的白色正方形框住"） | 29.31% |
| `frame` | 只描一圈白框，卡内照旧透出桌面 | 17.88% |
| `all` | 连名牌一起包进同一张卡 | — |

默认值是 `square` 而不是 `tight`：`tight` 白面更少（27.68% vs 29.31%），但用户说的是
"白色**正方形**"。`tight` 的 157x144 差 13px 才成方形，改成 157x157 只多 2209 px²，
却正好对上原话。想看方卡/紧卡/框 的差别直接看图：`ui_cards.png`（五联对比，A=现状、B=tight、
C=all、D=frame、E=square）。

> 两处默认值必须一起改：`live2d_assets.card_mode()` 和 `common.load_html(card=...)`，
> 还有 `ui.html` 里"模板标记没被替换"时的 JS 兜底。**三处不一致的话，谁漏传一次 card
> 就会得到和其它地方不同的观感，而且很难查。**

`all` 的用途是**兜住不确定性**：名牌那块白我复现不出来、也就无法保证修掉，
把它并进一张白卡之后即使还在也看不见（白底上的白块等于不存在）。
代价是整卡变高、`--muted` 的 `online` 在白底上偏淡，需要再配文字颜色。

### 两个"做对了反而报错"的坑（这一轮各踩一次）

**1. 断言扫源码原文，会把我自己写的注释判成违规。**
这一轮删 `backdrop-filter` 的做法是"把那行删掉、在旁边写注释说明为什么删"，
而注释里就写着「这里曾经有一行 `backdrop-filter: blur(10px)`」。断言用的是
`'backdrop-filter:' not in UI` —— 于是**修得越认真、注释写得越清楚，越是 FAIL**。
第一次跑出来的 2 条 FAIL 全在注释上，代码本身是对的。

> 修法：断言前先剥 CSS 注释（`re.sub(r'/\*.*?\*/', '', UI, flags=re.S)`）。
> 断言要查的是**生效的 CSS**，不是散文。顺带解决"注释里的 `}` 会把类块切片提前截断"。
> 教训：**看到 FAIL 先确认它查的是不是你以为的那份文本**，别急着改代码。

**2. 首次读 alpha 会读早一步 —— 这条比上一条更坑，因为它看起来像"功能坏了"。**
`live2d.ready` 一翻真就立刻 `readPixels`，但那一帧**可能还没画进 framebuffer**，
于是读回全 0：轮廓 0 个、`alphaAt(角色中心)=0`、`coverage=0`，
`verify_silhouette_js.py` 的 26 条断言里当场 6 条 FAIL。

触发条件：三个验收脚本**连着跑**（机器忙）。单独重跑同代码 → 立刻 26/26。
也就是说这 6 条 FAIL 里**没有一个字是真的**。

> 修法：`phaseSil()` 里先等轮廓非空再往下走（20 × 150ms = 3 秒），
> 超过就当它真坏、带着空读数继续（那时才是真问题）。
> 现在结果文件里多打一行 `轮廓读重试 : N 次` —— `>0` 就是这次被重试救回来了，
> 看到它非 0 就知道当时机器有多忙，**不用再去怀疑代码**。

> ⚠️ 和上一轮那条「WebView2 自动化会话不可靠」是同一类问题：
> **读不到 ≠ 不存在**。判据必须能区分"东西坏了"和"我这一眼没看准"。

## 第五轮：**块状白的真正根因** = region 的 pad 环露出宿主 Form 的底色（`AIRI_WIN_BG`）

用户第四次反馈："块状白还在，对话框（气泡）也有"。这一轮不再猜机制，改成**量尺寸**，
一路量到窗口层面，最后用**真 WebView2 窗口 + 屏幕像素**做了 A/B。

### 1) 先量：三块"白"的几何

对用户截图做连通域 + 逐行扫描（`_shot5.py` / `_crop5.py`），把窗口定位到截图内原点
`(23,22)`（用名牌块反推），得到窗口内坐标：

| 亮块 | 窗口内位置 | 尺寸 | 颜色 | 矩形度 |
|---|---|---|---|---|
| 气泡 1 | `(30,70)-(251,115)` | 222×46 | 边上一圈 `rgb(217,212,214)`、靠外 `rgb(236,235,235)` | 0.28（内部被气泡盖住） |
| 气泡 2 | `(44,116)-(237,159)` | 193×44 | 同上 | — |
| 名牌 | `(119,386)-(162,425)` | 44×40 | `rgb(241,235,237)` | 0.955 |

放大看（`_crop5_plate.png`）：**Airi 背后一块方底、online 背后一块更宽的方底**，
两块都是直角矩形，而中间的气泡是圆角的 —— 圆角矩形套一个直角矩形，
这就是用户说的"对话框那块白"。

### 2) 再算：这三块正好等于「DOM 盒 + pad」

`ui.html collectUiRects()` 把每个 DOM 盒**加 pad** 之后上报给 `SetWindowRgn`：

```js
for (var i = 0; i < bubbles.length; i++) pairs.push([bubbles[i], 5]);   // 气泡 pad=5
if (nm) pairs.push([nm, 3]);   if (st) pairs.push([st, 3]);              // 名牌 pad=3
out.push([r.left - pad, r.top - pad, r.right + pad, r.bottom + pad]);
```

按 CSS 算出 DOM 盒再加 pad，与截图实测**逐个吻合**：

| 位置 | 算式 | 算出来 | 截图实测 |
|---|---|---|---|
| 气泡 | 高 = 8+8(padding) + 18.6(line) + 2(border) = 36.6；宽 212 | +5 → **222×46.6** | **222×46** ✓ |
| `Airi` | 13px 字 + 3px letter-spacing → 33×15.6 | +3 → **39×21.6** | **40×20** ✓ |
| `online` | 点 6 + margin 4 + 10px 字 ≈ 37；高 14 | +3 → **43×20** | **44×20** ✓ |

**结论：白块 = 窗口 region 的 pad 环。** region 之外整个窗口被剪掉（漏桌面），
pad 环在 region 之内、页面又什么都没画 —— 于是露出宿主窗口自己的底色。

### 3) 底色是谁的：pywebview 忘了一行

```python
# webview/platforms/winforms.py:286-292
if window.transparent and self.browser:
    self.SetStyle(WinForms.ControlStyles.SupportsTransparentBackColor, True)
    self.browser.DefaultBackgroundColor = Color.Transparent
else:
    self.BackColor = ColorTranslator.FromHtml(window.background_color)   # ← 只有非透明才赋值
```

走 `transparent=True` 时 **`Form.BackColor` 从来没被赋值**，保持 WinForms 默认的
`SystemColors.Control`，浅色主题下就是 **`#F0F0F0`**。
（WebView2 那层确实是透明的 —— 透明露出来的就是这张浅灰底。）

真窗口实测里这个色**原样出现**：`off` 那次 `top colors` 里赫然是 `#F0F0F0 0.95%`。

### 4) 顺带解释第三轮为什么"LWA_COLORKEY 挖不掉底色"

第三轮用 `SetLayeredWindowAttributes(LWA_COLORKEY, #202020)`：**键色猜错了**。
底色是 `#F0F0F0`（浅灰），拿 `#202020`（深灰）去挖，一个像素都不会动 ——
当时的结论被迫写成"只有整窗 LWA_ALPHA 有效"，于是绕道去关 Mica。

本轮又把这个坑原地复现了一次，作为反例留在数据里：
`probe_live_fix5.py` 的"外部对照"把键色取成画面里最亮的 `#FFFFFF`（而不是实测底色），
结果 **"与桌面一致" 52.41% → 35.58%（更差）**，还把模型自己的白美术挖出了洞。

> **键色必须等于宿主底色的实测值，不能凭"看起来像"。** 这条比结论本身更值钱。

### 5) 修法

`common.fix_window_background()`：

```python
form.BackColor     = Color.FromArgb(1, 2, 3)   # 画面上不可能出现的颜色
form.TransparencyKey = 同一个颜色                # WinForms 据此走 LWA_COLORKEY → 真挖洞
```

- 页面没画的像素变成**真洞**（桌面透出来），而且那些像素**自动点穿**；
- 键色选 `#010203` 而不是黑/白：万一某个页面像素恰好等于键色会被误挖，
  `#010203` 在设计里不可能出现；
- `AIRI_WIN_BG`：`key`（默认）/ `dark`（只把底色压成近黑、不打洞，退路）/ `off`（A/B 用）；
- 在 `standalone.py` / `main.py` 的 `_after_window_ready()` 里执行（Form 是
  `webview.start()` 内部才创建的，所以轮询等它出现，最多 10s；失败只告警）。

### 6) 真窗口 A/B（`probe_live_fix5.py`，读数是屏幕像素）

两个配置各起一次桌宠、屏幕截图、数像素。判据只有一个数：**有多少像素等于 `#F0F0F0`**。

| 指标 | `AIRI_WIN_BG=off`（复现） | `AIRI_WIN_BG=key`（修复） |
|---|---|---|
| 名牌区域 `#F0F0F0` | **1475 / 7000（21.07%）** | **0（0.00%）** |
| 气泡区域 `#F0F0F0` | 133（0.42%） | **0（0.00%）** |
| 整窗浅灰像素 | 4894（3.91%） | 2081（1.66%） |
| 出现最多的色 | `#FFFFFF` / **`#F0F0F0` 0.95%** | `#FFFFFF`（`#F0F0F0` 消失） |
| 与桌面像素一致 | 52.41% | **79.28%** |

放大对照：`_cmp5_名牌_off.png`（一块方底）vs `_cmp5_名牌_key.png`（**一个像素都没有**，
"Airi / online" 直接浮在桌面上）。整窗图：`live5_off.png` / `live5_key.png`。

`key` 那次剩下的 1.66% 是**模型自己的白美术**（蕾丝头饰、白书桌板）—— 它不是 bug，
而且正是底板要框住的东西。

### 7) pad 放宽（以前是越小越好，现在无所谓）

气泡 5 → **8**、名牌 3 → **4**。pad 环现在是真的透明了，留大一点反而对点击手感好。
⚠️ **气泡 pad 必须 ≥ 7**：`.bubble::after` 那个尾巴是向下伸 7px 的，
pad 小于它就会被 region 裁掉。

### 8) 顺手做掉的两件事

- **底板从白卡改成蓝色水族箱**（用户要求）：缸壁 2px 玻璃边 + 水面高光带 +
  `repeating-linear-gradient(102deg)` 斜射光柱 + 缸底沙地 + 4 颗上浮气泡，
  **全纯 CSS 渐变**，不用 `filter` / `backdrop-filter` / `mix-blend-mode`
  （透明窗口上这三样都会出事）。`frame` 模式会 `display:none` 掉缸内装饰只留边框。
  预览：`ui_preview_square.png` / `ui_preview.png`（`probe_ui_preview.py` 渲染真页面）。
- 验收：`verify_click_region` 通过、`verify_silhouette_js` **26/26**、`verify_pet_ui` 通过、
  `node --check` 通过。

### 9) 踩到的两个坑（都不是产品问题，是工具问题）

1. **同一条消息里对同一个文件发两次编辑，会丢一次。** 本轮 `standalone.py` 的
   `_after_window_ready` 改上了、import 没改上；`main.py` 正好相反 —— 于是真窗口跑出
   `NameError: name 'fix_window_background' is not defined`。**改同一文件要串行。**
2. **`probe_live_fix5.py` 第一版只按 pid 找窗口，一个都没命中**（日志里明明有 hwnd）。
   改成"标题 `== 'Airi'` 或 pid 命中"双判据，并把这一轮看到的顶层窗口原样打出来 ——
   否则下次还是只有一句没信息量的 FAIL。



## 第六轮：点击/拖拽回归 —— 颜色键整窗鼠标穿透，路线整体弃用

用户报：**"点击没反应，也不能拖动"**（v0.3.3 之后）。本轮全部用可数判据定位。

### 1) 功能探针（`probe_input6.py`，真发鼠标事件）

点一下数"变化像素"（气泡 ≈上千，噪声几十），拖一下看 `GetWindowRect` 位移。
逐点 `WindowFromPoint` 画出"哪些地方能点到"。

### 2) 决定性证据（全部真窗/隔离实测）

| 实验 | 结果 | 结论 |
|---|---|---|
| 隔离实验：干净 Form 赋 .NET `TransparencyKey`（`winbg_fix6.txt`） | `GetLayeredWindowAttributes` → `flags=LWA_ALPHA alpha=0` | alpha=0 = 不可见 + 全穿透，不是颜色键 |
| 真窗 `AIRI_WIN_BG=key`：逐点 WindowFromPoint（`live_fix6.txt`） | 窗口内 8/8 采样点全部命中**桌面**（Program Manager） | **颜色键让 WebView2 内容整窗穿透** —— "看得见点不着"的机制 |
| 手动 `SetLayeredWindowAttributes(LWA_COLORKEY,#010203)` 五种写法（`colorkey_test2.txt`） | 键色读回**全部 `#000000`** | 挖洞行为在本机不可控 |
| 黑匣子（`_live6_*.log`） | `bridge ping FAIL "api 未就绪"` 且 `pywebviewready` 永不触发 | 探针环境下桥未注入（用户环境不受此限，见下） |

### 3) 决策与修复（v0.3.4）

1. **颜色键路线整体弃用**：`AIRI_WIN_BG` 默认 `key` → **`dark`**（只压黑底色，
   完全不碰分层 —— 输入路径与 v0.3.2 一致）。
2. `.NET` 属性访问全部 `BeginInvoke` 到 GUI 线程（WinForms 非线程安全；
   `TransparencyKey` 内部 `RecreateHandle()`，后台线程调用有搞挂 GUI 的风险）。
3. `collectUiRects()` pad 收窄到 1px、气泡尾巴单独成矩形 —— 细黑边替代块状白
   （**第七轮已再升级**：pad 归零 + 圆角/多边形同形状 region + 页面不透明绘制，
   黑边彻底消灭，见下）。
4. **JS 黑匣子**：`window.onerror`/`unhandledrejection` 上报 + `WindowAPI.ping()`
   桥自检。再出"点击没反应"，`[airi-js]` 日志能直接分辨页面崩 / 桥断 / 没命中。

### 4) 探针环境的教训

- 探针里 pywebview 6.2.1 透明模式的桥始终不注入（最小页面也复现，沙箱内外一致）；
  用户环境（VS Code 启动）历史上桥是通的。**探针测不了桥时，别把"探针里桥死"
  当成产品缺陷的证据，也别当成没缺陷的证据 —— 只能依赖用户侧黑匣子日志。**
- 探针跑分时用户在打《鸣潮》：全屏游戏盖在桌宠上方，`WindowFromPoint` 全命中
  游戏窗口、截图全黑。**真窗探针前先确认前台窗口。**


## 第七轮：黑边彻底消灭 —— region 与页面绘制**同形状** + 全不透明绘制

用户报："黑边又回来了"。第六轮"pad 收窄到 1px"只是把块状白压成细黑边，
没解决本质：**dark 模式下 region 里凡页面没画的像素都露黑底 Form**。
黑边有三个来源，本轮逐一修掉：

1. **方形 region × 圆角元素 = 弧形黑缺角**。气泡 r=14、名牌 r=10、
   角色卡 r=16，region 是方的，四角露出弧形黑。修法：
   `_apply_window_region` 重写，支持 `CreateRoundRectRgn`（按
   `border-radius` 缩放对齐）与 `CreatePolygonRgn`（气泡尾巴三角形），
   纯矩形协议保持向后兼容（verify_click_region 的单矩形断言原样通过）。
2. **半透明像素跟黑底混合**。气泡底 0.92、渐变边框 0.2~0.55、尾巴 0.45、
   缸壁 0.85 —— 全部换算成"混在黑上的等效不透明色"或改为实色。
   关键规则：**凡是要进 region 的绘制，一律不透明**。
3. **pad 环整圈露黑**。pad 归零，靠形状对齐而不是余量兜底。

顺带修掉的存量 bug：**右键菜单从来没进过 region** —— 弹出后被裁成
"看不见的菜单"。现在弹出/收起都重推形状，菜单矩形（rad=10）并入。

名牌从"黑方块"（Form 底透出来）变成 `.pet-plate` 自画的圆角板：
不透明深蓝底 + 粉描边，观感一致但终于名正言顺。

另：第六轮用户报"找不到启动按钮"的根因是 **package.json 的 UTF-8 BOM**
（PS `Set-Content -Encoding UTF8` 自带 BOM，扩展扫描器解析失败后
**静默跳过整个扩展**，exthost.log 无任何报错）。教训：
改 manifest 一律用不带 BOM 的写法；排查"扩展不加载"要 grep 全部窗口
日志看本扩展 ID 有没有激活记录，而不是等报错。


## 怎么跑

**最省事：双击这两个 bat**（不用管 PowerShell 还是 cmd）。

| 双击 | 作用 |
|---|---|
| `1_run_browser.bat` | 起本地服务器 + 用默认浏览器打开。**这条路已实测通过**，先看这个 |
| `2_run_webview2.bat` | 真实目标环境（pywebview + WebView2）。**分两趟跑**，见下 |

`2_run_webview2.bat` 跑两趟是因为它们回答两个不同的问题：

| 趟 | 窗口配置 | 回答的问题 |
|---|---|---|
| **A** | 普通不透明窗口、**页面上保留绿色日志** | WebView2 到底能不能给出可用的 WebGL 上下文？ |
| **B** | 无边框 + 透明 + 置顶（= 桌宠真实配置） | 真实配置下也能渲染吗？ |

A 趟结束后会停下来等你按键，再跑 B 趟。两趟的日志分别存到
`result_A_opaque.txt` / `result_B_transparent.txt`，最后自动用记事本打开。

- **A 趟看到模型** → WebView2 的 WebGL 没问题。
- **A 趟是全白窗口、只有绿字日志** → 看最后一行绿字停在哪一步，那就是断点。
- **B 趟模型浮在桌面上、没有边框** → 真实配置也通，可以动手集成。

等价的手动写法（PowerShell）：

```powershell
# 依赖只装在这个 venv 里，不污染你的环境
$venv = "C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe"

# === 现在最该跑的五条：验证的是桌宠本体，不是探针页 ===

# 0a) 生产静态服务器冒烟（快，不需要浏览器）
& $venv smoke_pet_server.py

# 0b) 桌宠本体 ui.html 端到端渲染（真 Chrome + 真 WebGL）
& $venv verify_pet_ui.py

# 0c) 窗口镂空 + 点击说话：宿主侧断言 + 真窗口区域 before/after 读回
& $venv verify_click_region.py

# 0d) 前端交互（轮廓采集 / 点击命中 / 拖拽不误触发）—— 真 Chrome 合成事件
& $venv verify_silhouette_js.py

# 0e) 复刻 VS Code 扩展的启动条件（真 Python312 + 真 cwd + 真 standalone.py）
#     会短暂弹出桌宠窗口，看到结果会自动收掉
& $venv probe_vscode_launch.py

# 附：想知道用户截图里那块白到底是什么颜色（读剪贴板）
& $venv analyze_screenshot.py

# === 以下是最初的探针页验证，留作回归 ===

# 1) Chrome 无头，自动化断言 —— 这是确定性的那一份
& $venv verify_headless.py

# 2) WebView2 / pywebview —— 需要你自己看着窗口跑
$env:PROBE_MANUAL = "1"
& $venv run_webview.py

# 3) 启动器自检：bat 是否纯 ASCII、环境变量契约是否一致
& $venv check_launchers.py
```

> ⚠️ 别在 **cmd.exe** 里敲 `$venv = "..."` 这种 PowerShell 语法 ——
> 会报 `'$venv' 不是内部或外部命令`。用上面的 bat，或先开 PowerShell 窗口。

`?showcase=1` 是探针页的一个开关：跑完所有断言后藏起日志、接管 ticker
持续播放 idle 动作，把角色留在屏上。不加这个参数的话，探针 1 秒内跑完就关窗，
人根本来不及看。**注意 showcase 只在探针成功时才藏日志**，失败时红字错误会留在屏上。

产物：

| 文件 | 内容 |
|---|---|
| `verify_result.txt` | Chrome 侧完整日志 + 断言（探针页） |
| `report.json` | 页面回报的原始测量值 |
| `shot.png` | 页面自绘的渲染截图 |
| `pet_ui_result.txt` | **桌宠本体** `ui.html` 的端到端日志 + 断言 |
| `pet_ui_shot.png` | **桌宠本体**的渲染截图（页面自绘） |
| `pet_server_result.txt` | 生产静态服务器冒烟结果（14 条断言） |
| `vscode_launch_result.txt` | 复刻 VS Code 扩展启动条件的结果（10 条断言） |
| `click_region_result.txt` | 窗口形状 + 点击说话结果（31 条断言） |
| `region_before.png` / `region_after.png` | 真窗口**裁形状前/后**的实证截图（注意：**area 外变透明是 Form 底色被裁掉造成的**，与屏幕上的 Mica 无关） |
| `silhouette_js_result.txt` | 前端交互结果（26 条断言） |
| `silhouette_report.json` | 前端回报的原始测量值（轮廓矩形 / alphaAt / 点击 / 拖拽） |
| `transparency_result.txt` | 三条透明路线（backcolor / transkey / colorkey / ellipse）的对照结论 |
| `shot_pixels.txt` | 用户剪贴板截图的地面真值（色块面积占比） |
| `webview_result.txt` | WebView2 侧最近一次日志 |
| `result_A_opaque.txt` / `result_B_transparent.txt` | 两趟 WebView2 的日志（bat 自动拷贝） |
| `launcher_check.txt` | 启动器自检结果 |
| `hits.log` | 服务器收到的每个请求 |
| `trace.jsonl` | 页面增量追踪（卡住时用来看断点） |

## 设计要点

**页面自己回报结果。** 探针页把测量值（含像素采样与截图）POST 回服务器，
所以验证不依赖 CDP / playwright 之类的自动化库。截图是**页面自己画的**，
不是外部截屏 —— 它证明的是"真的画出来了"，而不是"窗口在那儿"。

**服务走 `http://127.0.0.1`，不走 `file://`。** 这是可行性的前提，不是随便选的：
`file://` 下 WebView2 对 fetch/XHR 一律 CORS 拦截，`model3.json` 引用的 12 个
文件全都取不到。同源 HTTP 一次解决，顺带绕开 WebView2 对本地磁盘脚本的路径白名单。

**像素统计必须先量 alpha、再合成背景。** 先把画布铺上底色再数不透明像素，
会得到 100% —— 断言退化成恒真。截图与测量用两张独立的画布。

**断言要带对照。** 模型带物理模拟，冻结动作后画面仍会漂移。测"表情是否还原"
必须同时测一个"什么都不做"的对照窗口，否则把物理漂移误判成还原失败：

```
物理漂移基准  78809 px / 48 帧
表情还原残差  80783 px          <- 仅差 2.5%，说明表情确实清理干净了
```

## 分层定位工具

WebView2 出问题时，别猜，逐层剥：

| 脚本 | 用途 |
|---|---|
| `verify_pet_ui.py` | **桌宠本体** `ui.html` 端到端渲染验证（真 Chrome + 真 WebGL + 真资源服务器） |
| `verify_click_region.py` | 窗口形状与点击说话：宿主侧断言（31 条）+ 真窗口 `GetWindowRgn` before/after 读回 |
| `verify_silhouette_js.py` | **前端交互**（轮廓采集 / `alphaAt` 命中 / 点击 / 拖拽）—— 真 Chrome 合成 MouseEvent，假 `pywebview.api` |
| `probe_transparency.py` | 透明三条路线对照探针（`backcolor` / `transkey` / `colorkey` / `layered` / `ellipse`） |
| `analyze_screenshot.py` | 读剪贴板截图做网格采样 + 颜色直方图，用来给"眼前看到的现象"定地面真值 |
| `smoke_pet_server.py` | 生产静态服务器冒烟：`start_asset_server()` + 闭包全量可达 + **8 条路径越界攻击** |

### 活窗口取证（第三轮新增，专治"用户机器上和探针里不一样"）

这一类脚本**不重启桌宠**，直接对着用户正在跑的那个窗口做实验 ——
前一轮的教训就是"探针窗口通过 ≠ 用户的窗口通过"。

| 脚本 | 用途 |
|---|---|
| `probe_live_window.py <pid>` | 枚举顶层/子窗口、`GetClientRect`/`GetWindowRgn`/`exstyle`，并用 `PrintWindow(PW_RENDERFULLCONTENT)` 逐层抓图，回答"谁在铺底" |
| `probe_live_alpha.py <pid>` | **显示/隐藏差分**：同一矩形抓两次屏幕，算出"窗口区域有多少像素等于它背后的桌面"。这是唯一可信的透明判据 |
| `probe_live_fix.py <pid> [--apply]` | 把父窗口 region 复制到子窗口，看屏幕变不变（结论：不变） |
| `probe_live_fix2.py <pid>` | 逐个试 `SetWindowRgn+SWP_FRAMECHANGED` / `WS_EX_LAYERED+colorkey`，测完还原 |
| `probe_live_fix3.py <pid>` | **判别"改动到底生效没有"**：整窗 `LWA_ALPHA=0` 应该让窗口消失；顺带 `WindowFromPoint` 验点穿、逐子窗口读 region |
| `probe_live_fix4.py <pid>` | **决定性实验**：`DwmSetWindowAttribute(38, NONE)` 前后对比 → `0.00% → 90.49%`，再改回 2 复现 |
| `probe_verify_fix.py --pid N --x X --y Y` | 验证修复：读回 backdrop 值 + 隐藏/显示差分（会先把窗口挪开，避免和用户在跑的实例重叠） |
| `probe_bridge.py` | 对比不同解释器里 pywebview 的内部结构（`BrowserView.instances` 是否可用） |
| `find_webview.py` | 定位每个解释器的 pywebview 安装路径与版本，并列出 `winforms.py` 里所有 `transparent`/`BackColor` 相关行 |

产物：`live_window.txt`、`live_alpha.txt`、`live_fix*.txt`、`live_top.png`、`live_kid*.png`、
`alpha_visible*.png`、`alpha_hidden.png`、`fix4_micaoff.png`、`verify_fix.png`。
| `probe_vscode_launch.py` | **复刻 VS Code 扩展的启动条件**（同一个 Python + 同一个 cwd）跑真 `standalone.py` |
| `check_launchers.py` | 启动器自检：bat 是否纯 ASCII、环境变量契约、窗口参数解析、venv 与静态资源 |
| `smoke.html` + `PROBE_PAGE=smoke.html` | 每步都回报，看断在第几步 |
| `tiny_pywebview.py` | 最小判定：pywebview 窗口本身能否加载 |
| `bisect_window.py` | 窗口参数（frameless/transparent/on_top/url）二分 |
| `probe_js.py` | 用 `evaluate_js` 直读 JS 状态，绕开 HTTP 通道 |
| `matrix.py` | Chromium 开关矩阵，测 WebGL 是否可救 |

## 已知坑

**「API 返回成功」不等于「屏幕上生效」，判透明只认屏幕像素。** 这是本项目
代价最大的一条。`SetWindowRgn` 返回 1、`GetWindowRgn` 读回的形状逐值相同，
`LWA_COLORKEY` 也返回 1 —— 屏幕上那块方块一像素没动。原因是 **DWM 的 Mica
背景材质不在窗口的 GDI 重定向表面里**，这些 API 全都作用在表面上。
唯一可信的判据是**隐藏/显示同一个矩形做差分**：
"窗口区域里有多少像素等于它背后的桌面"。详见「第三轮」。

**别用 `PrintWindow` 判透明。** `PrintWindow(hwnd, PW_RENDERFULLCONTENT)`
抓的是窗口的合成内容，**不体现窗口形状（region）裁剪**，
也不体现 DWM 那层的最终结果。判"屏幕上长什么样"必须 `BitBlt(GetDC(NULL))`。
（反过来说：`BitBlt` 又抓不到 WebView2 的 DirectComposition 面。
两者各有盲区，**知道自己在用哪一个**才是关键。）

**`GetWindowRgn` 在 `user32`，`GetRgnBox` 在 `gdi32`。** 混了就是
`AttributeError: function 'GetWindowRgn' not found` —— 这个坑踩过两次。

**64 位下句柄一定要显式声明 `restype`/`argtypes`。** 少声明一个
`gdi32.GetPixel.argtypes = [c_void_p, c_int, c_int]`，HDC 就按 C `int` 传，
句柄超过 2^31 直接 `OverflowError: int too long to convert`。
`BitBlt` 是 9 个参数（dest DC + 4 个 int + src DC + 2 个 int + rop），
`SetWindowPos` 是 7 个 —— 数错就报 `takes 10 arguments (9 given)`。

**.bat 必须纯 ASCII（已实测踩过）。** 第一版 bat 里写了中文（UTF-8 无 BOM），
双击后 cmd.exe 用 OEM 码页（简体中文 Windows 是 936/GBK）解码批处理文件，
中文字节被拆开、连后面的换行一起吃掉，于是 cmd 把中文碎片当命令执行：

```
'90"' 不是内部或外部命令，也不是可运行的程序
'xxx?echo' 不是内部或外部命令，也不是可运行的程序
```

**整个脚本解析直接崩掉，Python 一行都没跑** —— 看起来像"测试失败"，
其实测试从未开始。`chcp 65001` 放在文件开头也救不了：cmd 读取批处理时用的
是它自己的码页。结论：**bat 内容全用英文，中文只出现在 Python 写出的 UTF-8
结果文件里。** `check_launchers.py` 会把这条规则当断言跑（逐字节检查 ≤ 0x7F）。

**pywebview 会丢弃你的 Chromium 开关。** `webview/platforms/edgechromium.py:82`
直接给 `props.AdditionalBrowserArguments` 赋值，该属性**优先于**
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 环境变量。想传开关必须拦它的 setter
（`probe_js.py:patch_browser_args` 里有现成实现）。

**无边框 + 透明窗口在渲染失败时是"隐形"的。** 里面什么都没画出来时，
用户既看不见窗口、也没有标题栏可以关，只能去任务管理器杀进程。所以：
`2_run_webview2.bat` 的 A 趟故意用**普通不透明窗口 + 保留日志**；
并且 `run_webview.py` 里有 `PROBE_HARD_TIMEOUT` 兜底守护线程
（默认 `TIMEOUT_S + 30` 秒），保证 `webview.start()` 万一卡死也会强制退出进程，
批处理不会永久挂住。

**在帧外读参数，恒等于基线值 —— 会得出"眨眼驱动没跑"的错误结论（已踩）。**

第一版 `verify_pet_ui.py` 用 `requestAnimationFrame` 循环从外部读 `ParamEyeLOpen`，
9 秒采了 1299 帧，结果 `min=1 max=1`，看起来眨眼完全没生效。**但驱动是好的。**
原因是帧序里 `loadParameters()` 在最后，把基线恢复回去了 —— 从帧外任何时刻读，
看到的都是被恢复过的 1。

改成在 `core.saveParameters()` 的**接缝处**读，真相立刻出来：

```
接缝处 eye 值 : postMin=0 postMax=1 blinkFrames=32 completeCycles=1   ← 真的眨到 0 又睁开
参数          : count=247 eyeL=21 eyeR=22
saveParameters 被引擎调用次数: 1385
```

所以断言要写成：`postMin < 0.2`（闭过）+ `postMax > 0.9`（**重新睁开**）+
`completeCycles >= 1`。**`postMax` 那条最关键** —— 接缝挂错位置时
`ParamEyeLOpen` 会被每帧累乘，眼睛会永久闭死，`postMin` 照样是 0，只查 `postMin` 抓不住。
（一次完整循环 = 先降到 <0.5，再回到 >0.9。）

**扩展启动桌宠时，你的所有输出都被丢掉（`stdio: 'ignore'`）。**
`src/extension.ts:launchStandalonePet()`：

```js
spawn(python, [path.join(extensionRoot, 'desktop_pet', 'standalone.py')], {
    cwd: petDir, detached: true, stdio: 'ignore', windowsHide: true,
})
```

`stdio:'ignore'` 意味着 `print` 的任何东西**都不会出现在任何地方** ——
VS Code 的输出面板、终端、日志文件，全都没有。于是「Live2D 悄悄降级成立绘」
这类问题在用户侧**零痕迹可查**，只能靠猜。这正是我踩到的：判断"扩展这条路通不通"
时，我一开始完全看不到子进程说了什么。

修法是让 `standalone.py` 自己落盘一份：`log()` 同时 `print` + 追加到
`desktop_pet/.airi-pet.log`（已 gitignore，每次启动重写）。
排查 Live2D 为什么不生效**先看这个文件**。

> 注意别指望改成 `stdio:'pipe'` —— 那就得在扩展里持续消费管道，否则缓冲区满了子进程会卡死。
> 而且扩展是 `detached: true` 起的，扩展窗口一关管道就断了。落盘是这里唯一稳的方案。

**客户端断开会被 `http.server` 打成一整段 traceback（已修）。**
WebView2 关窗/刷新时正在传输的请求会被 reset，`socketserver.handle_error()` 默认
把 `ConnectionResetError` / `BrokenPipeError` 当异常打出来：

```
Exception occurred during processing of request from ('127.0.0.1', 4379)
Traceback (most recent call last):
  ...
ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。
```

看起来像崩溃，其实只是客户端走了。**`log_message` 关掉挡不住这个** —— 它是另一个钩子。
`common.py` 的 `_AssetHandler` 与 `standalone.py` 的 `AiriHandler` 都已加
`handle_error()` 覆盖，只吞这四类收尾异常，真正意外的错误照旧抛出。
（这个坑是在 `probe_vscode_launch.py` 杀进程时冒出来的 —— 因为要杀进程看结果，
恰好把这条正常收尾路径触发了。）

**本模型的关键约束**（预检已自动报出）：

- 8 个动作的 `motion3.json` **全部** `"Loop": true` → `motionFinish` 永不触发，
  宿主必须自己按 `Meta.Duration` 定时收尾
- **没有声明 Cubism HitAreas** → 点击判定不能用引擎的 `hitTest()`，
  得从渲染结果的 alpha 通道提轮廓（**已实现**，见「第二轮」）
- `Expressions` 按 `Name`（中文）匹配，不是文件名

**抓屏读数不可信 —— 分层窗口 / 硬件加速窗口尤其（踩了两次）。**

第一次透明探针用 `GetPixel(GetDC(NULL))` 读像素，同一坐标两次运行读出
`#F0F0F0` 和 `#000000` 两个值。**分层窗口的屏幕 DC 读取本身就没有可信语义。**
改成窗口 DC + `BitBlt` 之后又撞上第二个：

**`BitBlt(GetDC(NULL), ...)` 抓不到 WebView2 的内容。** WebView2 走
DirectComposition 合成，**不在屏幕 DC 里**。结果是所有变体的窗口区域一律读成黑色——
这几乎让我得出「layered 会把 WebView2 弄坏」的**假结论**，
也把第一次 `TransparencyKey` 的判定作废了。

正确做法是 `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)`（`0x02`），
它能拿到 DirectComposition 的内容。**并且**：像素只用来做**旁证**，
判定要落在系统可读的属性上（`GetWindowRgn` / `GetRgnBox` / `GetWindowLong`）——
属性读数不依赖渲染路径，也不依赖会话环境。

> 附带一条：本自动化会话里 WebView2 不渲染，基线探针窗口
> `PrintWindow` 读回 **0/2500 非黑像素**。**别把它当成被测代码的缺陷**——
> 判定 WebView2 可用性只能由人在交互桌面上跑。

**在 PowerShell 里对同一个文件并行发两条 `Edit` 会互相覆盖。**
一次改 `probe_transparency.py` 时，两条编辑在同一条消息里发出，
结果 `ellipse` 分支根本没写进文件（运行打印 `applied: nothing (baseline)`）。
原因是两条编辑各自读到同一份旧内容、各自写回。
**改成：要么串行，要么整文件重写。**

**`SetWindowRgn` 的空区域会变成"零面积窗口"。** 一旦所有矩形都被裁掉，
窗口在屏幕上就彻底不存在了 —— 既看不见也点不到。所以
`_apply_window_region()` 对「空矩形」和「全部裁光」两种情况都**直接拒绝**，
宁可退化成整块灰框。

## 素材来源与许可

素材取自本地检出：**`S:\Relax_event\dsh_live2d\dsh-pet-live2d`**
（remote = `A8Chann/dsh-pet-live2d`，原作者仓库；用户在 GitHub 上的 fork 是
`Hancard/dsh-pet-live2d`）。已用 SHA256 核对：本目录 `vendor/live2d-vendor.js` 与
`model/ds-whale-girl/` 下的文件与该检出**逐字节一致**，所以本目录的验证结果
直接适用于那份检出。

| 内容 | 来源 | 许可 |
|---|---|---|
| 模型 `model/ds-whale-girl/` | `A8Chann/dsh-pet-live2d` → `dsh-live2d-pet/pets/ds-whale-girl/` | **CC BY-NC-SA 4.0**（署名 · 非商用 · 同协议） |
| `vendor/live2d-vendor.js` | 同上，pixi.js + untitled-pixi-live2d-engine | MIT |
| `vendor/live2dcubismcore.min.js` | Live2D 官方 CDN | Live2D 专有，**不可再分发** |

模型版权链：**上善无形**（鲸鱼娘角色原作 OC「溟月」）→ **ZipZipPipe**（DeepSeek 女仆二创）
→ **氵六青**（本模型 Live2D 制作）。个人自用没问题，**商用需分别取得各权利人授权**。

> ⚠️ `live2dcubismcore.min.js` 是专有运行时，**不要提交进 git 仓库**。
> 正式实现时应由 Python 在启动时抓取并缓存到本地（见下方「落地要点」）。

### 素材不在 git 里 —— 新克隆如何重建

`vendor/live2dcubismcore.min.js` 与 `model/` 都在 `.gitignore` 里，所以一个新鲜
clone 直接跑会 preflight 失败。重建两步：

```powershell
# 1) Cubism Core（带 UA 抓取 + 校验，见 fetch_core.py）
& $venv fetch_core.py

# 2) 模型 + vendor 引擎：从本地检出拷贝
$src = "S:\Relax_event\dsh_live2d\dsh-pet-live2d\dsh-live2d-pet"
Copy-Item "$src\lib\live2d-vendor.js" .\vendor\ -Force
Copy-Item "$src\pets\ds-whale-girl" .\model\ -Recurse -Force
```

拷完可以跑 `check_launchers.py` 确认静态资源齐全。

## 作者自带的资料 —— 正式集成前必读

那份检出里有两样东西比自研探针更权威，**别重复造轮子**：

**`.dsh/skills/cubism-engine/SKILL.md`** —— 引擎行为的第一手记录：

- 帧序是 `saveParameters() → update() → loadParameters()`，**`loadParameters()` 在最后**。
  要叠加自己的参数必须挂在 `saveParameters()` 之后，否则会被本帧快照吃进基线、下一帧再叠一次，**永远关不掉**。
- 按名字查参数必须走 `core._model.parameters.ids`（字符串数组）；
  引擎包装层的 `getParameterIndex(string)` 传字符串**永远 miss**。
- `pointX/pointY` 满量程是 **±30**（不是 ±1 —— 拿 ±1 去试看起来就像参数是死的）。
- **引擎的眨眼从未跑过**：只要本帧有动作在驱动参数，`eyeBlink` 就被整个跳过，
  而这个模型的 idle 循环几乎永远在驱动参数。所以加载时必须 `eyeBlink: false`，自己按帧驱动。
- 无条件 `stopAllMotions()` 会毁掉交叉淡入淡出（旧动作被瞬间清掉就没有东西可以淡出），
  只在**重播同一个 group+index** 时才停。
- 动作停下后**不会**还原它写过的参数；待机循环只驱动 247 个参数里的 89 个。

**`.dsh/skills/verification-signals/SKILL.md`** —— ⚠️ **直接影响本探针的可信度边界**：

> 「视觉验证必须读引擎在帧内写入的参数值，禁止像素比对/哈希比对。」

作者的理由：宠物一直在呼吸眨眼，任意两帧都不会逐字节相同 → 哈希信号**恒为真**；
冻结 rAF 也停不住 Pixi ticker，后续每格读的是同一张陈旧帧。**他们因此对同一个问题
给出过两次互相矛盾的结论。** 权威做法是在 `core.update` 末尾采样参数值
（或直接读 `window.__dshLive2dPet.drawn(id)`）。

对本探针的适用性，要说清楚：

- 探针用于回答**「这条管道能不能渲染出东西」**，量的是**不透明度覆盖率**与
  「画面是否随帧变化」——这是**活跃度/覆盖率**信号，不是效果断言，**结论仍然成立**。
- 但探针里「表情施加 / 还原」那两条**确实是像素残差**做的。我用「什么都不做」的
  对照窗口（物理漂移基准）做了缓解，可作者的经验说明**这类测量对这个模型先天不可靠**。
  正式集成时若需要断言「表情效果对不对」，**必须换成帧内参数读法**，不要沿用这里的写法。

**`tools/browser-test/`** —— 作者自带的 CDP 自动化测试台（`server.mjs` / `run-suite.mjs` /
`harness.js` + 近百个 driver，覆盖 motion / exp / gaze / mask / param 等）。
后续做回归验证应优先复用它，而不是继续扩写本探针。

## 落地要点 —— 逐条对照当前实现状态

| # | 要点 | 状态 |
|---|---|---|
| 1 | 废掉 `common.py:create_temp_html()`，窗口指向 `http://127.0.0.1:<port>/` | ✅ 唯一的结构性改动，已完成 |
| 2 | 静态路由：`/` 给 ui.html、`/pet-assets/*` 给模型闭包（白名单 + realpath 挡 `..`）、`/vendor/*` 给两个 js | ✅ `common.py:_make_handler()` |
| 3 | Cubism Core 由 Python 启动时抓取 + 缓存 + 校验 | ✅ `live2d_assets.ensure_core()` |
| 4 | 动作状态机（`stopAllMotions()` 时序、FORCE/IDLE 优先级、忽略启动 250ms 内的 `motionFinish`、按 `Duration` 定时收尾） | ⬜ **未做** —— 目前只用 `motion('Idle', 0, IDLE)` |
| 5 | 情绪映射 | ✅ `idle/greeting/angry/happy/surprised` 全部对上 |

关于第 3 条，实测结论再强调一次：**裸请求返回 403，带浏览器 UA 才 200**
（207155 字节，`Access-Control-Allow-Origin: *`）。作者的记录是直接 200 ——
说明网关/CDN 策略会变，**所以一定要带 UA，并且校验响应**
（长度 + 含 `Live2DCubismCore` 符号），别把登录页/错误页当成功存下来。
`ensure_core()` 还会优先复用 `~/.dsh/pets/...` 下已有的副本。

### 已完成情绪映射（第 5 条）

桌宠现在的情绪词表就五个（`python_backend/corpus.py`），已 1:1 映射：

| 现有情绪 | 模型表情（按 `model3.json` 里的中文 `Name` 匹配） |
|---|---|
| `idle` | 无（`resetExpression()` 回待机） |
| `greeting` | 开心兴奋 |
| `angry` | 生气 |
| `happy` | 星星眼 |
| `surprised` | 感叹号 |

> 引擎一次只挂一个表情，所以原作者文档里"开心兴奋 + 星星眼"这种**组合**
> 取其中更能表达情绪的那个即可。

模型实际有 **44 个表情 + 8 个动作**（idle / hammer / bubble-gum / spray-water /
open-case / selfie / selfie-quick / ketchup）。除 idle 外的动作**尚未接线** ——
这是后续增强，不是缺口。

### 还没做的

- **非 idle 动作接线**：现在只播 idle。想做"生气时挥锤子""打招呼时自拍"这类，
  要走第 4 条的动作状态机。

### 已经做完的（原「还没做的两件事」）

- ~~**点击命中判定**~~ ✅ 已实现：模型没有声明 Cubism HitAreas，
  引擎 `hitTest()` 用不了 → 从渲染结果的 alpha 通道提轮廓 + 分区，
  见「第二轮：窗口镂空 + 点击说话」。

### 一个提醒：别再扩写自研探针

作者的 `.dsh` 里有 **`tools/browser-test/`**（`server.mjs` / `run-suite.mjs` /
`harness.js` + 近百个 driver，覆盖 motion / exp / gaze / mask / param），
比本目录这套自研探针完整得多。后续做回归验证应优先复用它。
