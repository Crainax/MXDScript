# MHScript Python Migration

这是把旧的魔盒 KM 脚本逐步迁移到 Python + 易键鼠的实验项目。

项目目标位置：`D:\Project\MXDScript`。

当前阶段只做了项目初始化、资料研读和 `D:\Project\MHScript\Tool\开包.km` 的行为分析，尚未开始转换实现。

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

更多细节见 `docs/architecture.md`、`docs/legacy_open_package.md` 和 `docs/questions_for_user.md`。
