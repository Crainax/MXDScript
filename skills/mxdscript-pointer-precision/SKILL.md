---
name: mxdscript-pointer-precision
description: Reusable guidance for MXDScript YiJianShu hardware mouse automation that must temporarily disable Windows "Enhance pointer precision" and restore it safely. Use when modifying GUI launchers, live hardware scripts, pause/resume behavior, mouse movement accuracy, or Windows SystemParametersInfo-based pointer acceleration handling in this project.
---

# MXDScript Pointer Precision

## Core Rule

For live YiJianShu mouse-control scripts, temporarily disable Windows "Enhance pointer precision" before sending hardware mouse movement. Restore the user's original mouse acceleration settings when the script pauses, stops, exits, or errors.

Do not change this setting during dry-run mode.

## Existing Project Implementation

Use `src/mhscript_yjs/runtime/mouse_settings.py` instead of rewriting WinAPI code.

Important types:

```python
MousePointerPrecisionManager
MouseAccelerationSettings
```

Current behavior:

- `disable_temporarily()` saves the current `(threshold1, threshold2, speed)` values the first time it runs, then sets `(0, 0, 0)`.
- `restore()` restores the saved values exactly, not merely "enable acceleration".
- The GUI stores one manager per run so pause/resume can restore and disable repeatedly without losing the original state.

## WinAPI Details

Use `SystemParametersInfoW`:

```python
SPI_GETMOUSE = 0x0003
SPI_SETMOUSE = 0x0004
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002
```

The value is a three-int array:

```text
threshold1, threshold2, speed
```

Windows "Enhance pointer precision" is disabled by setting all three to zero:

```text
0, 0, 0
```

Typical enabled values may look like:

```text
6, 10, 1
```

Always save the actual original values and restore those exact values.

## Integration Pattern

For a live GUI/script:

1. On start/resume: call `disable_temporarily()` before opening or driving the hardware script.
2. On pause: call `restore()` after requesting pause.
3. On normal finish, stop, window close, or exception: call `restore()` in the cleanup path.
4. In dry-run mode: skip mouse precision changes.
5. Log `mouse_precision_saved`, `mouse_precision_disabled`, and `mouse_precision_restored` at INFO level.

In GUI launchers, restore from both worker completion and window destroy paths. Redundant restore calls are acceptable and should be idempotent.

## Validation Notes

Prefer non-destructive validation first:

```python
from mhscript_yjs.runtime.mouse_settings import MousePointerPrecisionManager
print(MousePointerPrecisionManager().get())
```

Only toggle the setting during an explicit live run or targeted manual test. If a test toggles it, restore the original values in `finally`.

## Common Pitfalls

- Do not assume "restore" means setting `(6, 10, 1)`. Restore the user's saved original values.
- Do not change the setting for dry-run tests.
- Do not leave the setting disabled if the script errors while a device is open.
- Keep this separate from YiJianShu DLL calls; pointer precision is a Windows host setting, not an `msdk.dll` setting.
