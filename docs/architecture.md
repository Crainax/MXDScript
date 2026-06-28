# 新项目架构草案

## 迁移目标

旧项目是魔盒 KM 脚本集合，主要靠 `FindPic -> KeyPress/MoveD/LeftClick -> Delay` 的过程式流程完成自动化。新项目应把这些职责拆开：

- `drivers`: 只封装易键鼠 `msdk.dll`，负责键盘、鼠标、延迟等硬件输入。
- `vision`: 负责截图、模板匹配、颜色/图片查找，对应 KM 的 `FindPic`。
- `windows`: 负责查找 `MapleStory` 窗口、读取窗口尺寸、计算窗口内搜索区域。
- `runtime`: 负责日志、随机延迟、统一异常、退出原因、运行上下文。
- `scripts`: 按旧仓库目录组织具体迁移脚本，例如 `scripts/tool/open_package.py`。

这样做的好处是：本次只迁移 `Tool/开包.km`，以后迁移 `System`、`Union` 或角色脚本时可以复用窗口、识图、输入和日志层。

## 建议源码结构

```text
src/mhscript_yjs/
  drivers/
    yjs.py              # ctypes wrapper for msdk.dll
    dry_run.py          # no-hardware action recorder
    keycodes.py         # Windows VK code mapping
  vision/
    matcher.py          # OpenCV template matching
    screenshot.py       # mss screenshot capture
    types.py            # Region/ImageGroup/MatchResult
  windows/
    maple.py            # find MapleStory window and client bounds
  runtime/
    logging.py          # rotating file + console logs
    timing.py           # delay and random delay helpers
  scripts/
    tool/
      open_package.py   # migrated Tool/开包.km state machine
```

## 日志策略

每次运行生成一个独立日志文件，建议格式：

```text
logs/open_package_YYYYMMDD_HHMMSS.log
```

文件日志记录 DEBUG 及以上的完整诊断信息；GUI 实时日志只显示 `IMPORTANT` 及以上的关键中文事件，例如“将卡牌转成精华”“开包获取10张卡牌”和结束统计，避免循环找图日志刷屏。

建议记录字段：

- 启动参数：配置文件、Python 位数、DLL 路径、窗口标题、图片根目录
- 窗口信息：左上角、宽高、搜索区域
- 识图结果：图片组名、匹配坐标、相似度、耗时
- 操作行为：按键、移动坐标、点击坐标、延迟区间
- 状态机：当前阶段、`Jing/Shi/Confirm` 切换、重试计数
- 退出原因：窗口不存在、设备打开失败、模板缺失、长时间无匹配、用户中断

## 配置策略

提交 `config/default.toml` 作为项目默认值；本机私有路径、DLL 路径、VID/PID、分辨率放到 `config/local.toml`，该文件已被 `.gitignore` 忽略。

打包版首次运行会在 exe 同目录落地 `assets/`、`config/`、`vendor/msdk/`。相对 `image_root = "assets"` 会优先按 exe 同目录解释，因此另一台电脑只拿到 exe 也能得到同一套模板、默认配置和易键鼠 DLL。当前模板保留 BMP；匹配器已经支持 PNG alpha 遮罩，后续可以在确认无像素差异后再迁移格式。

## 测试策略

- 设备层：对 `msdk.dll` 调用做薄封装，运行前可用 dry-run 设备替身记录动作，不实际控制鼠标键盘。
- 识图层：用固定截图和 BMP 模板做离线匹配测试。
- 脚本层：把 `开包.km` 的阶段逻辑写成状态机，单元测试只验证状态跳转和动作序列。

## 当前不做的事

- 不改动旧仓库 `D:\Project\MHScript`。
- 不提交 `msdk.dll`、`.lib`、`.h` 等资料库二进制/头文件。
- 不迁移大型角色循环脚本。
- 不直接实机控制易键鼠，除非显式传入 `--live`。

## 本轮用户确认

- 项目目录应迁移到 `D:\Project\MXDScript`，之后都在该目录继续。
- 易键鼠是单头。
- 允许把 64 位 `msdk.dll` 放入本项目 `vendor/msdk/msdk.dll`，但该 DLL 不提交 Git。
- 游戏窗口大小可能变化，但游戏画面像素模板稳定，所以应继续基于窗口客户区动态计算搜索区域。
- 旧魔盒 `MoveD` 语义已用 `命令说明52.1.pdf` / `.tmp_pdf_text.txt` 确认：`MoveD x,y,jitter,speed` 是带轨迹移动到目标坐标。

## 下一步建议

第一版 `Tool/开包.km` 迁移已经实现。下一步建议先用 dry-run 运行一次，确认窗口、截图和模板匹配日志正常；再实现一个小的 `device_probe`，自动尝试 `M_Open(1)`、`M_ScanAndOpen()` 并记录 VID/PID；最后用 `--live` 小范围实测易键鼠动作和坐标。
