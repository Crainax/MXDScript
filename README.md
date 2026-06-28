# MHScript Python Migration

这是把旧的魔盒 KM 脚本逐步迁移到 Python + 易键鼠的实验项目。

项目目标位置：`D:\Project\MXDScript`。

当前阶段已经完成 `D:\Project\MHScript\Tool\开包.km` 的第一版 Python 迁移实现。默认运行是 dry-run：会找 MapleStory 窗口、截图、找图并写日志，但不会控制易键鼠；加 `--live` 后才会打开设备并发送键鼠动作。

## 已确认的旧项目结构

- 旧仓库位置：`D:\Project\MHScript`
- KM 文件数量：约 270 个，不包含 `.git/.venv/.codex/.cursor` 等隐藏/工具目录
- 主要目录：`System`、`Tool`、`Union`、`Lynn`、`Lara`、`FirePoison`、`Event`
- 本次试点脚本：`D:\Project\MHScript\Tool\开包.km`
- 图片资源：`E:\MHImg\UI` 与 `E:\MHImg\Event\202602`，本次脚本引用的 BMP 均已存在
- 旧魔盒 API 参考：`D:\Project\MHScript\命令说明52.1.pdf` 与 `D:\Project\MHScript\.tmp_pdf_text.txt`
- 已确认 `MoveD x,y,jitter,speed` 是带轨迹移动到目标坐标，不是相对移动

## 新项目目标结构

```text
config/                 默认配置与本机私有配置样例
docs/                   架构、迁移记录、待确认问题
logs/                   运行日志输出目录
src/mhscript_yjs/       Python 包源码
  drivers/              易键鼠 msdk.dll 的 ctypes 封装
  vision/               截图、模板匹配、FindPic 等价实现
  windows/              MapleStory 窗口发现、坐标区域计算
  runtime/              日志、延迟、错误处理、脚本运行上下文
  scripts/tool/         Tool 类 KM 的 Python 入口
tests/                  单元测试与可离线验证的状态机测试
vendor/msdk/            本机放置 msdk.dll 的位置，不提交 DLL
```

## 开包脚本运行方式

### GUI exe

已提供一键测试 GUI，打包输出位置：

```text
D:\Project\MXDScript\dist\MXDScriptOpenPackage\MXDScriptOpenPackage.exe
```

双击后只有一个主按钮：

- `开始`: 启动开包脚本，默认是易键鼠 live 模式，会实际控制硬件。
- `暂停`: 运行中点击同一个按钮会实时暂停。
- `继续`: 暂停后点击同一个按钮继续运行。

也可以按 `F10` 全局快捷键开始/暂停/继续。窗口不在前台时也会响应。

live 模式启动时会自动关闭 Windows 鼠标设置里的“提高指针精确度”；暂停、停止或结束时会恢复启动前的鼠标设置。

关闭窗口会请求停止脚本并释放设备。日志写入 exe 同目录下的 `logs/`，配置文件和 DLL 位于 exe 同目录下的 `config/` 与 `vendor/msdk/`。

重新打包命令：

```powershell
cd D:\Project\MXDScript
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_open_package_gui.ps1
```

### 命令行

首次运行前先安装依赖，建议用项目内虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

在项目目录 `D:\Project\MXDScript` 下运行：

```powershell
$env:PYTHONPATH='src'
python -m mhscript_yjs.scripts.tool.open_package --skip-delays --max-iterations 1
```

上面是 dry-run 诊断模式，不会控制键鼠。真正连接易键鼠运行时使用：

```powershell
$env:PYTHONPATH='src'
python -m mhscript_yjs.scripts.tool.open_package --live
```

每次运行会在 `logs/open_package_YYYYMMDD_HHMMSS.log` 写详细日志。dry-run 仍然需要游戏窗口可见，也需要安装 `mss`、`numpy`、`opencv-python` 等依赖，因为截图和找图由 Python 完成。

## 后续实现原则

- 旧 KM 里的找图、按键、点击、延迟行为先逐条建模，再写 Python。
- 易键鼠只负责硬件级键鼠输入；截图和找图由 Python 侧完成。
- 每个脚本入口都要写详细日志，包含窗口坐标、匹配到的图片、点击坐标、阶段切换、失败重试和退出原因。
- 私有路径和硬件参数放在 `config/local.toml`，不提交到 Git。

## 当前已知运行环境

- 易键鼠类型：单头。
- Python 位数：当前检测为 64-bit，实机运行应配套 64 位 `msdk.dll`。
- 游戏窗口大小可能变化，但游戏画面像素模板稳定。
- 设备打开方式与 VID/PID 尚待用探测脚本确认；单头且只插一个盒子时，可先尝试 `M_Open(1)` 或 `M_ScanAndOpen()`。

## 已实现模块

- `drivers.yjs`: `msdk.dll` 的 ctypes 封装，支持打开设备、设置绝对移动分辨率、移动、左键、Enter 和释放按键。
- `drivers.dry_run`: 记录动作但不控制硬件，便于先看状态机和日志。
- `windows.maple`: 按标题片段查找窗口，并读取客户区坐标和尺寸。
- `vision.matcher`: 对应旧 `FindPic` 的模板匹配，支持四角同色透明规则。
- `scripts.tool.open_package`: 对应 `Tool/开包.km` 的状态机迁移。

更多细节见 `docs/architecture.md`、`docs/legacy_open_package.md` 和 `docs/questions_for_user.md`。
