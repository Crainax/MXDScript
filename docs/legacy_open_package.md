# `Tool/开包.km` 行为分析

源文件：`D:\Project\MHScript\Tool\开包.km`

## 外部依赖

- 窗口标题：`MapleStory`
- UI 图片：
  - `E:\MHImg\UI\Yes.bmp`
  - `E:\MHImg\UI\OK.bmp`
  - `E:\MHImg\UI\OK2.bmp`
- 活动图片：
  - `E:\MHImg\Event\202602\Jing1.bmp`
  - `E:\MHImg\Event\202602\Jing1_2.bmp`
  - `E:\MHImg\Event\202602\Shi1.bmp`
  - `E:\MHImg\Event\202602\Shi2.bmp`

上述图片在当前机器上已检查存在。

## 初始化逻辑

1. 用 `GetWindowPos logoX, logoY, *MapleStory*, *` 查找冒险岛窗口。
2. 找到后用 `GetWindowSize intX, intY, *MapleStory*, *` 取得宽高。
3. 基于窗口左上角和窗口大小计算全窗口搜索区域：
   - `x1 = logoX`
   - `y1 = logoY`
   - `xEnd = logoX + intX`
   - `yEnd = logoY + intY`
4. 找不到窗口时输出错误、弹窗、蜂鸣并暂停。

## 主循环状态

脚本本质是三类 UI 状态的优先级状态机：

1. 优先找确认按钮：`Yes/OK/OK2`
2. 找 `Jing1/Jing1_2`
3. 找 `Shi1/Shi2`

核心变量：

- `noFindCount`: 连续未有效推进计数，达到 `1800` 时退出。
- `c`: 记录上一轮非确认阶段，初始为 `2`。
  - `c = 2` 后，确认阶段结束优先找 `Shi`。
  - `c = 3` 后，确认阶段结束优先找 `Jing`。
- `a`: 3 秒等待窗口计数，`10 * 300ms`。
- `b`: 等待阶段是否成功找到目标。

## 操作流程

### 确认按钮分支

1. 找到 `Yes/OK/OK2` 后按 `Enter`。
2. 随机延迟 `240-330ms`。
3. 根据 `c` 决定接下来 3 秒内等待 `Shi` 或 `Jing`。
4. 找到后移动到 `intX + 10, intY`，延迟 `90ms`，左键点击。
5. 切换 `c`：`2 -> 3` 或 `3 -> 2`。

### Jing 分支

1. 找到 `Jing1/Jing1_2` 后点击右偏 `10px` 的位置。
2. 设置 `c = 2`。
3. 3 秒内优先等待确认按钮，避免频繁重按 `Jing`。

### Shi 分支

1. 找到 `Shi1/Shi2` 后点击右偏 `10px` 的位置。
2. 设置 `c = 3`。
3. 3 秒内优先等待确认按钮，避免频繁重按 `Shi`。

### 无匹配分支

找不到确认、`Jing`、`Shi` 时：

1. `noFindCount += 1`
2. 延迟 `300ms`
3. `noFindCount >= 1800` 时退出循环

## Python 迁移要点

- `FindPic` 应迁移为 `vision.match_any(images, region, threshold=1.0)`。
- `KeyPress Enter` 应迁移为易键鼠 `M_KeyPress2(handle, 13, 1)`。
- `LeftClick` 应迁移为易键鼠 `M_LeftClick(handle, 1)`。
- `DelayRandom 240,330` 可优先用 Python `random.uniform` + `time.sleep`，或调用 `M_DelayRandom`。
- 需要记录每次识图和动作日志，尤其是匹配坐标、阶段变量 `c`、`noFindCount` 和退出原因。

## 需要确认的风险点

- `MoveD intX+10,intY,2,16` 已根据 `D:\Project\MHScript\.tmp_pdf_text.txt` 确认为“带轨迹移动到目标坐标”，不是相对移动；相对移动对应旧 API 的 `MoveR`。
- KM 的 `FindPic` 使用 24-bit BMP 和 `sim=1.00` 精确匹配。OpenCV 模板匹配的阈值需要实测，不能直接假设 `1.0` 等价。
- 如果游戏窗口存在 DPI 缩放、非客户区边框或多显示器偏移，Python 截图坐标需要单独校准。

## 旧 API 关键确认

- `GetWindowPos` 返回顶级窗口客户区坐标，不含标题栏和菜单栏。
- `GetWindowSize` 返回客户区宽高。
- `FindPic` 未找到时结果坐标为负数。
- `FindPic` 的四角同色透明规则需要在 Python 模板匹配中兼容：图片四角颜色相同则该颜色视为透明色。
