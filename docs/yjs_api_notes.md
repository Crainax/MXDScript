# 易键鼠开发资料摘记

资料来源目录：`E:\OneDrive\文档\pdf\易键鼠开发资料`

已阅读的关键文件：

- `易键鼠单双头三头开发资料\请一定先双击打开看看.txt`
- `易键鼠单双头三头开发资料\检测鼠标移动是否准确的方法.txt`
- `易键鼠单双头三头开发资料\32位DLL_盒子开发例程（推荐使用）\python3例程\pythonDemo.py`
- `YJS64_test\main.py`
- `32位DLL_盒子开发例程（推荐使用）\DLL文件\msdk.h`
- `64位DLL_盒子开发例程（有开发经验的选用）\DLL和h文件\msdk.h`

## 关键规则

- 易键鼠盒子本身不能保存脚本；Python 程序必须在主控机运行，并向盒子发送键盘/鼠标命令。
- 盒子只模拟键盘鼠标，不负责截图、找图、取色或识别游戏状态。
- 找图逻辑必须由 Python 自己完成，推荐拆到 `vision` 层。
- DLL 分 32 位和 64 位；Python 进程位数必须与 `msdk.dll` 位数一致。
- 当前环境检测到 Python 为 64 位，因此后续实机运行应使用 64 位 `msdk.dll`。
- 用户确认当前易键鼠为单头设备。

## 常用 API

设备：

- `M_Open(int Nbr)`: 按端口号打开默认 VID/PID 设备。
- `M_Open_VidPid(int Vid, int Pid)`: 按 VID/PID 打开设备。
- `M_ScanAndOpen()`: 扫描并打开第一个设备。
- `M_Close(handle)`: 关闭设备。

单头且只插一个盒子时，建议先实现设备探测：

1. 尝试 `M_Open(1)`。
2. 如果失败，尝试 `M_ScanAndOpen()`。
3. 打开成功后调用 `M_GetVidPid` 和 `M_GetDevSn`，把结果写入日志。
4. 再把稳定结果写入 `config/local.toml`。

键盘：

- `M_KeyPress2(handle, key_code, count)`: Windows VK 码单击，新开发推荐。
- `M_KeyDown2(handle, key_code)`: 按下。
- `M_KeyUp2(handle, key_code)`: 弹起。
- `M_ReleaseAllKey(handle)`: 释放所有按键。

鼠标：

- `M_LeftClick(handle, count)`: 左键点击。
- `M_MoveTo2(handle, x, y)`: 单头/同机模式下按系统当前鼠标位置移动到坐标。
- `M_ResolutionUsed(handle, width, height)`: 使用绝对移动前设置分辨率。
- `M_MoveTo3(handle, x, y)`: 绝对移动，有轨迹。
- `M_MoveTo3_D(handle, x, y)`: 绝对移动，一步到位。

延迟：

- `M_Delay(ms)`
- `M_DelayRandom(min_ms, max_ms)`
- `M_SetParam(handle, type, min, max)` 可设置按键、鼠标点击和移动轨迹的随机延迟参数。

## Python 封装建议

- 用 `ctypes.WinDLL` 加载 DLL，并显式声明 `restype` / `argtypes`。
- 设备类支持上下文管理器：`with YjsDevice(...) as device: ...`，退出时释放按键并关闭句柄。
- 对每个 DLL 调用检查返回值，失败时抛出带 API 名和参数的异常。
- 提供 `DryRunDevice`，不连接硬件，只记录动作，方便测试状态机和日志。
- `M_KeyPress2` 使用 Windows VK 码；`Enter` 是 `13`。
