---
name: mxdscript-daily-automation
description: Project-specific guidance for MXDScript daily automation, coordinate movement, image matching, GUI log filtering, and KM-to-Python behavior parity. Use when modifying CombineMain/daily_script, coordinate_mover, coordinate_detector, TemplateMatcher, character movement controllers, or GUI script/debug panels.
---

# MXDScript Daily Automation

## Core Rules

- Preserve KM timing semantics for live YiJianShu runs. `KeyDown -> Delay -> KeyUp` must keep the real delay, especially for movement. Skipping delay in live mode turns holds into taps and causes stuck movement.
- Treat `skipDelays` as a dry-run accelerator. In live mode, ignore it for scripts that send hardware input and log a warning.
- Keep dry-run and live behavior separate. Dry-run may skip sleeps and record actions; live must drive the hardware with realistic holds, release keys on cleanup, and restore mouse settings.
- Do not commit assets or screenshot/template churn unless the user explicitly asks. The user often replaces local `assets/**` images while tuning recognition.

## Movement State Machine Lessons

The Python movement controller mirrors KM `Move`:

```text
GetXY
AntiJam
if x < target - JumpRange: MoveRight
elif x > target + JumpRange: MoveLeft
elif x < target - tolerance: KeyDown Right, Delay a, KeyUp Right
elif x > target + tolerance: KeyDown Left, Delay a, KeyUp Left
elif y < target - yTolerance: MoveDown
elif y > target + yTolerance: MoveUp
else: reached
```

For micro-movement, KM computes:

```text
a = distance * 52
clamp a to 52..1800 ms
```

If logs show `skip_delay_ms` between `key_down Right` and `key_up Right` during a live run, that is a bug. The expected log should show a real `delay_ms`.

Coordinates should refresh the MapleStory client window each round. Coordinate scripts may cache a recent position for a few missed frames; ordinary image matching should return immediate results unless a script explicitly needs short miss tolerance.

## Image Matching Lessons

- Current project matching is pixel-tolerance based, not OpenCV correlation for normal `FindPic` behavior.
- BMP templates must not infer transparent pixels from same-colored corners. Only PNG alpha should define transparency.
- For coordinate detection, match `MapAnchor.bmp`, `Me.bmp`, `Teleport.bmp`, and `Rune.bmp` against the same minimap screenshot in one cycle.
- Log best/accepted matches at DEBUG when diagnosing misses, but keep GUI default filters at `IMPORTANT` and above.

## Rune Automation Lessons

- `MatchResult.x/y` are screen coordinates because `TemplateMatcher` adds the search region offset. Movement targets must use `Rune.bmp - MapAnchor.bmp`, matching the coordinate detector's `relativeX/relativeY`. A log like `screen=(143,1866)` should become a movement target like `(106,118)`, never `(143,1866)`.
- Keep `ReleaseRune` shared. `combine_main` and `leveling` should call the same Python rune release flow instead of letting one script fall back to the legacy KM `Pause`.
- Press `PageDown`, wait for the rune UI, recognize all four directions, freeze the four-key sequence, then press. Do not re-recognize after the first direction key because the first arrow may disappear.
- Reject unsafe recognition before pressing any direction key. Use both the group score and per-slot confidence; on rejection, save a screenshot under `auto_screenshots/rune_solver`, press `Space` twice to exit the UI, wait about 3 seconds, and retry.
- Only set `RuneCooldown` after leaving the rune position and confirming `Rune.bmp` is gone for multiple frames. If verification itself fails, pause for manual handling rather than assuming success.
- Rune logs should be human-readable Chinese at `IMPORTANT` or higher for key decisions: target conversion, frozen directions, unsafe recognition screenshot, retry, success, and manual pause.

## GUI Runtime Lessons

- The GUI can start another script while one is running; starting a new script requests stop on the current script first.
- Internal KM `Pause` should pause the script through `request_pause()`, not terminate it.
- GUI real-time logs may receive INFO events, but the visible log panel should filter by level. Default visible levels are `IMPORTANT`, `WARNING`, `ERROR`, and `CRITICAL`; user choices are remembered in browser localStorage.
- Keep testing tools modular:
  - `识别图片`: repeated image hit/miss diagnostics.
  - `检测坐标`: passive coordinate display only.
  - `移动坐标`: one-shot movement test with mutually exclusive `Move` / `MoveB`.

## Validation Checklist

Before committing behavior changes:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m unittest discover -s tests
cd gui_web; npm run build
```

For live input fixes, also release keys after tests:

```python
from mhscript_yjs.core.config import load_config
from mhscript_yjs.drivers.yjs import YjsDevice

device = YjsDevice(settings=load_config().yjs)
device.open()
try:
    device.release_all_keys()
finally:
    device.close()
```

## Common Pitfalls

- Do not use `NullSleeper` for live movement.
- Do not hide INFO at the backend event handler if the GUI needs opt-in debugging; filter in the frontend instead.
- Do not combine passive coordinate detection with active movement threads. A movement target belongs in a dedicated script so normal start/stop controls own its lifecycle.
- Do not revert unrelated local asset changes while committing code.
