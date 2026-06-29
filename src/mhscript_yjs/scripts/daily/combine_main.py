from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mhscript_yjs.core.config import ProjectConfig
from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.drivers.yjs import YjsDevice
from mhscript_yjs.runtime.control import NullRunControl, RunControl, StopRequested
from mhscript_yjs.runtime.logging import log_important
from mhscript_yjs.runtime.timing import NullSleeper, Sleeper
from mhscript_yjs.scripts.daily.combine_main_source import SOURCE as COMBINE_MAIN_SOURCE
from mhscript_yjs.vision.matcher import TemplateMatcher
from mhscript_yjs.vision.screenshot import MssScreenCapture
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo, find_window


DAILY_SCRIPT_ID = "daily_script"
DEFAULT_MATCH_THRESHOLD = 0.95
MIN_MATCH_THRESHOLD = 0.5
MAX_MATCH_THRESHOLD = 1.0
DEFAULT_DAILY_OPTIONS = {
    "dailyQuest": True,
    "gugu": True,
    "summerDaily": True,
    "otherDaily": True,
    "matchThreshold": DEFAULT_MATCH_THRESHOLD,
}
JOB_DETECTION_THRESHOLD = 0.99


@dataclass(frozen=True)
class DailyScriptResult:
    exit_reason: str
    steps: int
    modules: dict[str, str] = field(default_factory=dict)


class KmPauseRequested(RuntimeError):
    pass


class DailyRunner:
    def __init__(
        self,
        *,
        config: ProjectConfig,
        device: InputDevice,
        matcher: TemplateMatcher,
        sleeper: Sleeper,
        logger: logging.Logger,
        options: dict[str, Any] | None = None,
        control: RunControl | None = None,
        window_info: WindowInfo | None = None,
        capture: MssScreenCapture | None = None,
    ) -> None:
        self.config = config
        self.device = device
        self.matcher = matcher
        self.sleeper = sleeper
        self.logger = logger
        self.options = _coerce_options(options)
        self.match_threshold = float(self.options["matchThreshold"])
        self.control = control or NullRunControl()
        self.window_info = window_info
        self.capture = capture
        self.vars: dict[str, Any] = {}
        self.subs = _parse_subs(COMBINE_MAIN_SOURCE)
        self.steps = 0
        self.modules: dict[str, str] = {}
        self._branch_eval_entries: set[int] = set()

    def run(self) -> DailyScriptResult:
        try:
            self.device.open()
            self._initialize_window()
            self._initialize_job()
            self._run_enabled_modules()
            return self._result("completed")
        except StopRequested:
            self.logger.info("daily_stop_requested steps=%s", self.steps)
            return self._result("stop_requested")
        except KmPauseRequested as exc:
            self.logger.warning("daily_pause_requested reason=%s", exc)
            return self._result("script_pause")
        finally:
            self.device.close()
            if self.capture is not None:
                self.capture.close()

    def _run_enabled_modules(self) -> None:
        self._run_module(
            "dailyQuest",
            "日常任务",
            lambda: (self.execute_sub("ReceiveQuest"), self.execute_sub("ClearQuest")),
        )
        self._run_module("gugu", "菇菇神社", self._run_gugu)
        self._run_module("summerDaily", "活动签到", lambda: self.execute_sub("SummerDaily"))
        self._run_module(
            "otherDaily",
            "其他每日",
            lambda: (
                self.execute_sub("HDDaily"),
                self.execute_sub("LegionDaily"),
                self.execute_sub("MileageDaily"),
                self.execute_sub("UseItem"),
                self.execute_sub("MallDaily"),
            ),
        )

    def _run_module(self, key: str, label: str, callback: Any) -> None:
        if not self.options[key]:
            log_important(self.logger, "[Daily] 跳过模块：%s", label)
            self.modules[key] = "skipped"
            return
        log_important(self.logger, "[Daily] 开始模块：%s", label)
        callback()
        log_important(self.logger, "[Daily] 完成模块：%s", label)
        self.modules[key] = "completed"

    def _initialize_window(self) -> None:
        window = self.window_info or find_window(self.config.maple_story.window_title)
        self.window_info = window
        self.vars.update(
            {
                "JobLynn": 1,
                "JobLara": 2,
                "CurrentJob": 0,
                "alterX": 0,
                "alterY": 0,
                "facing": 0,
                "startTime": 0,
                "RuneCooldown": 0,
                "patrolTime": 0,
                "questPointer": 0,
                "mapOrder": 0,
                "logoX": window.x,
                "logoY": window.y,
                "intX": window.width,
                "intY": window.height,
                "x1": window.x,
                "y1": window.y,
                "x2": window.x + 400,
                "y2": window.y + 330,
                "xEnd": window.right,
                "yEnd": window.bottom,
                "x3": window.right - 600,
                "y3": window.bottom - 105,
            }
        )
        log_important(
            self.logger,
            "[MapleStory] Window found at (%s, %s) Size: %sx%s",
            window.x,
            window.y,
            window.width,
            window.height,
        )

    def _initialize_job(self) -> None:
        log_important(self.logger, "[System] Starting Lynn/Lara job detection...")
        if self._find_pic(
            self._region("x3", "y3", "xEnd", "yEnd"),
            (
                r"E:\MHImg\Lynn\Feature.bmp",
                r"E:\MHImg\Lynn\Feature2.bmp",
            ),
            JOB_DETECTION_THRESHOLD,
            "intX",
            "intY",
            use_configured_threshold=False,
        ):
            self.vars["questPointer"] = 2
            self.vars["CurrentJob"] = self.vars["JobLynn"]
            log_important(self.logger, "[System] Detected job: Lynn")
        elif self._find_pic(
            self._region("x3", "y3", "xEnd", "yEnd"),
            (r"E:\MHImg\Lara\Feature.bmp",),
            JOB_DETECTION_THRESHOLD,
            "intX",
            "intY",
            use_configured_threshold=False,
        ):
            self.vars["questPointer"] = 0
            self.vars["CurrentJob"] = self.vars["JobLara"]
            log_important(self.logger, "[System] Detected job: Lara")
        else:
            raise RuntimeError("未检测到 Lynn/Lara 职业信息，无法继续日常脚本。")

        if self.vars["CurrentJob"] == self.vars["JobLynn"]:
            self.vars.update({"SpellOK": 0, "fireTime": 0, "WGTime": 0, "JumpRange": 24})
            log_important(self.logger, "[Lynn] Lynn job initialized")
        else:
            self.vars.update(
                {
                    "WGTime": 0,
                    "seedTime1": 0,
                    "ReleaseTime1": 0,
                    "JumpRange": 24,
                }
            )
            log_important(self.logger, "[Lara] Lara job initialized")
        log_important(
            self.logger,
            "[System] Job initialization completed for %s",
            self.vars["CurrentJob"],
        )

    def execute_sub(self, name: str) -> None:
        lowered = name.lower()
        if lowered == "gugu":
            self._run_gugu()
            return
        if lowered in {"initializejob", "detectjob"}:
            self._initialize_job()
            return
        try:
            lines = self.subs[lowered]
        except KeyError as exc:
            raise RuntimeError(f"Unknown CombineMain subroutine: {name}") from exc
        self._execute_lines(name, lines)

    def _execute_lines(self, sub_name: str, lines: list[str]) -> None:
        labels = _labels(lines)
        while_pairs = _while_pairs(lines)
        for_pairs = _for_pairs(lines)
        for_stack: list[list[int]] = []
        previous_branch_entries = self._branch_eval_entries
        self._branch_eval_entries = set()
        pc = 0
        try:
            while pc < len(lines):
                self._checkpoint()
                line = _strip_comment(lines[pc]).strip()
                if not line:
                    pc += 1
                    continue
                lower = line.lower()
                self.steps += 1
                if self.steps > 1_000_000:
                    raise RuntimeError(f"CombineMain execution step limit exceeded in {sub_name}.")
                if lower.startswith("rem ") or lower == "rem":
                    pc += 1
                    continue
                if lower in {"exit sub", "return"}:
                    return
                if lower == "break":
                    pc = _enclosing_pair_end(while_pairs, pc) + 1
                    continue
                if lower.startswith("goto "):
                    label = line.split(None, 1)[1].strip()
                    pc = labels[label.lower()] + 1
                    continue
                if _is_if_header(line):
                    condition = _if_condition(line)
                    if self._truthy(self._eval_expr(condition)):
                        pc += 1
                    else:
                        pc = self._jump_to_next_if_branch(lines, pc)
                    continue
                if _is_else_if(line):
                    if pc not in self._branch_eval_entries:
                        pc = _matching_end_if(lines, pc) + 1
                        continue
                    self._branch_eval_entries.discard(pc)
                    condition = _else_if_condition(line)
                    if self._truthy(self._eval_expr(condition)):
                        pc += 1
                    else:
                        pc = self._jump_to_next_if_branch(lines, pc)
                    continue
                if _is_else(line):
                    pc = _matching_end_if(lines, pc) + 1
                    continue
                if lower == "end if":
                    pc += 1
                    continue
                if lower.startswith("while "):
                    condition = line[6:].strip()
                    if self._truthy(self._eval_expr(condition)):
                        pc += 1
                    else:
                        pc = while_pairs[pc] + 1
                    continue
                if lower == "wend":
                    pc = _matching_pair_start(while_pairs, pc)
                    continue
                if lower.startswith("for "):
                    if not for_stack or for_stack[-1][0] != pc:
                        count = int(self._eval_expr(line[4:].strip()))
                        if count <= 0:
                            pc = for_pairs[pc] + 1
                            continue
                        for_stack.append([pc, count])
                    pc += 1
                    continue
                if lower == "next":
                    if not for_stack:
                        raise RuntimeError(f"Next without For in {sub_name}.")
                    top = for_stack[-1]
                    top[1] -= 1
                    if top[1] > 0:
                        pc = top[0] + 1
                    else:
                        for_stack.pop()
                        pc += 1
                    continue
                self._execute_statement(line)
                pc += 1
        finally:
            self._branch_eval_entries = previous_branch_entries

    def _jump_to_next_if_branch(self, lines: list[str], pc: int) -> int:
        branch = _next_if_branch_or_end(lines, pc)
        if branch is None:
            return len(lines)
        line = _strip_comment(lines[branch]).strip()
        if _is_else_if(line):
            self._branch_eval_entries.add(branch)
            return branch
        if _is_else(line):
            return branch + 1
        return branch + 1

    def _execute_statement(self, line: str) -> None:
        lower = line.lower()
        if lower.startswith("call "):
            self.execute_sub(_call_name(line))
            return
        if lower.startswith("traceprint "):
            log_important(self.logger, "%s", self._eval_expr(line[11:].strip()))
            return
        if lower.startswith("msgbox "):
            self.logger.warning("msgbox=%s", self._eval_expr(line[7:].strip()))
            return
        if lower.startswith("beep "):
            self.logger.warning("beep %s", line[5:].strip())
            return
        if lower == "pause":
            raise KmPauseRequested("CombineMain Pause command reached")
        if lower.startswith("delayrandom "):
            first, second = self._eval_args(line[12:])
            min_ms, max_ms = int(first), int(second)
            if min_ms > max_ms:
                min_ms, max_ms = max_ms, min_ms
            self.sleeper.delay_random_ms(min_ms, max_ms)
            return
        if lower.startswith("delay "):
            self.sleeper.delay_ms(int(self._eval_expr(line[6:].strip())))
            return
        if lower.startswith("keydown "):
            self.device.key_down(keycode(_key_name(line[8:])))
            return
        if lower.startswith("keyup "):
            self.device.key_up(keycode(_key_name(line[6:])))
            return
        if lower.startswith("keypress "):
            self.device.press_key(keycode(_key_name(line[9:])), 1)
            return
        if lower.startswith("moveto "):
            x, y = self._eval_args(line[7:])
            self.device.move_to(round(float(x)), round(float(y)))
            return
        if lower.startswith("moved "):
            args = self._eval_args(line[6:])
            if len(args) < 2:
                raise RuntimeError(f"Invalid MoveD statement: {line}")
            self.device.move_to(round(float(args[0])), round(float(args[1])), smooth=True)
            return
        if lower.startswith("mover "):
            args = self._eval_args(line[6:])
            if len(args) < 2:
                raise RuntimeError(f"Invalid MoveR statement: {line}")
            dx, dy = args[:2]
            self.device.move_relative(round(float(dx)), round(float(dy)))
            return
        if lower.startswith("leftclick"):
            rest = line[9:].strip()
            count = int(self._eval_expr(rest)) if rest else 1
            self.device.left_click(count)
            return
        if lower == "leftdown":
            self.device.left_down()
            return
        if lower == "leftup":
            self.device.left_up()
            return
        if lower.startswith("mousewheel "):
            self.device.mouse_wheel(int(self._eval_expr(line[11:].strip())))
            return
        if lower.startswith("wheel "):
            self.device.mouse_wheel(int(self._eval_expr(line[6:].strip())))
            return
        if lower.startswith("findpic "):
            self._execute_find_pic(line[8:])
            return
        assignment = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", line)
        if assignment:
            self.vars[assignment.group(1)] = self._eval_expr(assignment.group(2))
            return
        if lower.startswith("dim "):
            for name in line[4:].split(","):
                self.vars.setdefault(name.strip(), 0)
            return
        raise RuntimeError(f"Unsupported CombineMain statement: {line}")

    def _execute_find_pic(self, text: str) -> bool:
        args = _split_args(text)
        if len(args) < 10:
            raise RuntimeError(f"Invalid FindPic statement: {text}")
        region = Region.from_bounds(
            round(float(self._eval_expr(args[0]))),
            round(float(self._eval_expr(args[1]))),
            round(float(self._eval_expr(args[2]))),
            round(float(self._eval_expr(args[3]))),
        )
        raw_paths = _unquote(args[4]).split("|")
        threshold = float(self._eval_expr(args[6]))
        return self._find_pic(region, tuple(raw_paths), threshold, args[-2], args[-1])

    def _find_pic(
        self,
        region: Region,
        raw_paths: tuple[str, ...],
        threshold: float,
        out_x: str,
        out_y: str,
        *,
        use_configured_threshold: bool = True,
    ) -> bool:
        self._checkpoint()
        paths = tuple(self._resolve_image_path(path) for path in raw_paths)
        threshold = self._effective_threshold(threshold) if use_configured_threshold else threshold
        group = ImageGroup(name="|".join(path.name for path in paths), paths=paths, threshold=threshold)
        match = self.matcher.match_any(group, region)
        if match is None:
            self.vars[out_x.strip()] = -1
            self.vars[out_y.strip()] = -1
            return False
        self.vars[out_x.strip()] = match.x
        self.vars[out_y.strip()] = match.y
        self.logger.debug(
            "combine_findpic image=%s x=%s y=%s score=%.6f threshold=%.6f",
            match.image_path,
            match.x,
            match.y,
            match.score,
            threshold,
        )
        return True

    def _resolve_image_path(self, raw_path: str) -> Path:
        normalized = raw_path.strip().strip('"').replace("/", "\\")
        lower = normalized.lower()
        marker = "mhimg\\"
        if marker in lower:
            relative = normalized[lower.index(marker) + len(marker) :]
            candidate = self.config.maple_story.image_root / relative
            if candidate.exists():
                return candidate
        direct = Path(normalized)
        if direct.exists():
            return direct
        return self.config.maple_story.image_root / normalized

    def _run_gugu(self) -> None:
        self.vars["wJam"] = 0
        while True:
            if self._match_to("Gugu Mark2", (r"E:\MHImg\UI\Daily\Gugu\Mark2.bmp",), "logoX", "logoY"):
                if not self._close_stop_chat_for_gugu():
                    return
                if not self._complete_gugu_favor_flow():
                    return
                if not self._close_gugu_window():
                    return
                return

            self.vars["wJam"] += 1
            if self._match_to("Gugu Mark1", (r"E:\MHImg\UI\Daily\Gugu\Mark1.bmp",), "intX", "intY"):
                self._move_click(self.vars["intX"] + 100, self.vars["intY"] + 5)
            elif self._match_to(
                "Gugu Bulb",
                (r"E:\MHImg\UI\Bulb.bmp", r"E:\MHImg\UI\Bulb2.bmp"),
                "intX",
                "intY",
            ):
                self._move_click(self.vars["intX"], self.vars["intY"])
            else:
                if self.vars["wJam"] > 8:
                    self._gugu_warn("Missing images: Mark2.bmp, Mark1.bmp, Bulb.bmp")
                    return
                self.device.move_to(100, 100)
                self.sleeper.delay_random_ms(139, 142)

            if self.vars["wJam"] > 12:
                self._gugu_warn("Missing image after repeated clicks: Mark2.bmp")
                return

    def _close_stop_chat_for_gugu(self) -> bool:
        attempts = 0
        while True:
            if not self._match_to(
                "Gugu StopChat",
                (r"E:\MHImg\UI\Dialog\StopChat.bmp",),
                "intX",
                "intY",
            ):
                return True
            attempts += 1
            if attempts > 8:
                self._gugu_warn("Missing/blocked image: UI\\Dialog\\StopChat.bmp")
                return False
            self._tap("Esc", 69, 72)

    def _complete_gugu_favor_flow(self) -> bool:
        attempts = 0
        while True:
            self._move_click(self.vars["logoX"] + 314, self.vars["logoY"] + 27)
            self.sleeper.delay_random_ms(139, 142)
            self._move_click(self.vars["logoX"] + 141, self.vars["logoY"] + 391)
            self.sleeper.delay_random_ms(139, 142)

            if self._match_to(
                "Gugu Mark4",
                (
                    r"E:\MHImg\UI\Daily\Gugu\Mark4.bmp",
                    r"E:\MHImg\UI\Daily\Gugu\Mark4_2.bmp",
                ),
                "intX",
                "intY",
            ):
                self._move_click(self.vars["intX"] + 70, self.vars["intY"] + 5)
                self.sleeper.delay_random_ms(139, 142)
                self._page_gugu_rewards()
                return True

            attempts += 1
            if attempts >= 5:
                self._gugu_warn("Missing image: Mark4.bmp or Mark4_2.bmp")
                return False

    def _page_gugu_rewards(self) -> None:
        misses = 0
        while True:
            if self._match_to(
                "Gugu Mark5",
                (
                    r"E:\MHImg\UI\Daily\Gugu\Mark5.bmp",
                    r"E:\MHImg\UI\Daily\Gugu\Mark5_2.bmp",
                ),
                "intX",
                "intY",
            ):
                self._move_click(self.vars["intX"] + 100, self.vars["intY"] + 5)
                self.sleeper.delay_random_ms(139, 142)
                continue

            if self._match_to(
                "Gugu Next",
                (
                    r"E:\MHImg\UI\Daily\Gugu\Next.bmp",
                    r"E:\MHImg\UI\Daily\Gugu\Next_2.bmp",
                ),
                "intX",
                "intY",
            ):
                misses = 0
                self._tap("PageDown", 69, 72)
                continue

            misses += 1
            if misses > 5:
                return
            self.sleeper.delay_ms(200)

    def _close_gugu_window(self) -> bool:
        attempts = 0
        while True:
            if not self._match_to(
                "Gugu Mark2 close",
                (r"E:\MHImg\UI\Daily\Gugu\Mark2.bmp",),
                "intX",
                "intY",
            ):
                return True
            attempts += 1
            if attempts > 8:
                self._gugu_warn("Missing/blocked image: UI\\Daily\\Gugu\\Mark2.bmp close state")
                return False
            self._tap("Esc", 69, 72)
            self.sleeper.delay_random_ms(339, 342)

    def _match_to(
        self,
        name: str,
        paths: tuple[str, ...],
        out_x: str,
        out_y: str,
        threshold: float = 1.0,
    ) -> bool:
        matched = self._find_pic(
            self._region("x1", "y1", "xEnd", "yEnd"),
            paths,
            threshold,
            out_x,
            out_y,
        )
        self.logger.debug("gugu_match name=%s matched=%s", name, matched)
        return matched

    def _effective_threshold(self, threshold: float) -> float:
        return min(threshold, self.match_threshold)

    def _gugu_warn(self, message: str) -> None:
        self.logger.warning("[Gugu] %s", message)
        log_important(self.logger, "[Gugu] 警告：%s", message)

    def _tap(self, name: str, min_ms: int, max_ms: int) -> None:
        self.sleeper.delay_random_ms(39, 42)
        self.device.key_down(keycode(name))
        self.sleeper.delay_random_ms(min_ms, max_ms)
        self.device.key_up(keycode(name))
        self.sleeper.delay_random_ms(39, 42)

    def _move_click(self, x: float, y: float, count: int = 1) -> None:
        self.device.move_to(round(x), round(y))
        self.sleeper.delay_ms(50)
        self.device.left_click(count)

    def _region(self, left: str, top: str, right: str, bottom: str) -> Region:
        return Region.from_bounds(
            int(self.vars[left]),
            int(self.vars[top]),
            int(self.vars[right]),
            int(self.vars[bottom]),
        )

    def _eval_args(self, text: str) -> list[Any]:
        return [self._eval_expr(part) for part in _split_args(text)]

    def _eval_expr(self, expression: str) -> Any:
        expression = expression.strip()
        parts = _split_unquoted(expression, "&")
        if len(parts) > 1:
            return "".join(str(self._eval_expr(part)) for part in parts)
        expression = expression.replace("<>", "!=")
        env = _EvalEnv(self.vars, {"WaitKey": self._wait_key})
        return eval(expression, {"__builtins__": {}}, env)  # noqa: S307

    def _wait_key(self) -> int:
        raise RuntimeError("CombineMain manual WaitKey is not supported in the GUI runner.")

    def _truthy(self, value: Any) -> bool:
        return bool(value)

    def _checkpoint(self) -> None:
        self.control.wait_if_paused()
        if self.control.stop_requested():
            raise StopRequested("stop requested")

    def _result(self, exit_reason: str) -> DailyScriptResult:
        return DailyScriptResult(
            exit_reason=exit_reason,
            steps=self.steps,
            modules=dict(self.modules),
        )


class _EvalEnv(dict[str, Any]):
    def __init__(self, variables: dict[str, Any], functions: dict[str, Any]) -> None:
        super().__init__()
        self.variables = variables
        self.functions = functions

    def __getitem__(self, key: str) -> Any:
        if key in self.functions:
            return self.functions[key]
        return self.variables.get(key, 0)


def create_runner(
    *,
    config: ProjectConfig,
    dry_run: bool,
    skip_delays: bool,
    logger: logging.Logger,
    control: RunControl,
    options: dict[str, Any] | None = None,
) -> DailyRunner:
    device: InputDevice = (
        DryRunDevice(logger=logger) if dry_run else YjsDevice(settings=config.yjs, logger=logger)
    )
    capture = MssScreenCapture()
    matcher = TemplateMatcher(capture=capture, logger=logger)
    sleeper = (
        NullSleeper(logger=logger, control=control)
        if skip_delays
        else Sleeper(logger=logger, control=control)
    )
    return DailyRunner(
        config=config,
        device=device,
        matcher=matcher,
        sleeper=sleeper,
        logger=logger,
        options=options,
        control=control,
        capture=capture,
    )


def _coerce_options(options: dict[str, Any] | None) -> dict[str, Any]:
    raw = options or {}
    coerced: dict[str, Any] = {}
    for key, default in DEFAULT_DAILY_OPTIONS.items():
        if isinstance(default, bool):
            coerced[key] = bool(raw.get(key, default))
        elif key == "matchThreshold":
            coerced[key] = _coerce_match_threshold(raw.get(key, default))
        else:
            coerced[key] = raw.get(key, default)
    return coerced


def _coerce_match_threshold(value: Any) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MATCH_THRESHOLD
    if not math.isfinite(threshold):
        return DEFAULT_MATCH_THRESHOLD
    return max(MIN_MATCH_THRESHOLD, min(MAX_MATCH_THRESHOLD, threshold))


def _parse_subs(source: str) -> dict[str, list[str]]:
    subs: dict[str, list[str]] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for raw_line in source.splitlines():
        line = _strip_comment(raw_line).strip()
        sub_match = re.match(r"(?i)^sub\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if sub_match:
            current_name = sub_match.group(1).lower()
            current_lines = []
            continue
        if current_name is None:
            continue
        if line.lower() == "end sub":
            subs[current_name] = current_lines
            current_name = None
            current_lines = []
            continue
        current_lines.append(raw_line)
    return subs


def _labels(lines: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, raw_line in enumerate(lines):
        line = _strip_comment(raw_line).strip()
        if line.lower().startswith("rem "):
            result[line.split(None, 1)[1].strip().lower()] = index
    return result


def _while_pairs(lines: list[str]) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for index, raw_line in enumerate(lines):
        line = _strip_comment(raw_line).strip().lower()
        if line.startswith("while "):
            stack.append(index)
        elif line == "wend":
            start = stack.pop()
            pairs[start] = index
    return pairs


def _for_pairs(lines: list[str]) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for index, raw_line in enumerate(lines):
        line = _strip_comment(raw_line).strip().lower()
        if line.startswith("for "):
            stack.append(index)
        elif line == "next":
            start = stack.pop()
            pairs[start] = index
    return pairs


def _matching_pair_start(pairs: dict[int, int], end: int) -> int:
    for start, candidate_end in pairs.items():
        if candidate_end == end:
            return start
    raise RuntimeError(f"Could not find matching block start for line {end}.")


def _enclosing_pair_end(pairs: dict[int, int], pc: int) -> int:
    enclosing = [(start, end) for start, end in pairs.items() if start < pc < end]
    if not enclosing:
        raise RuntimeError("Break outside while loop.")
    return max(enclosing, key=lambda pair: pair[0])[1]


def _next_if_branch_or_end(lines: list[str], index: int) -> int | None:
    depth = 0
    for candidate in range(index + 1, len(lines)):
        line = _strip_comment(lines[candidate]).strip()
        lower = line.lower()
        if _is_if_header(line):
            depth += 1
        elif lower == "end if":
            if depth == 0:
                return candidate
            depth -= 1
        elif depth == 0 and (_is_else_if(line) or _is_else(line)):
            return candidate
    return None


def _matching_end_if(lines: list[str], index: int) -> int:
    depth = 0
    for candidate in range(index + 1, len(lines)):
        line = _strip_comment(lines[candidate]).strip()
        lower = line.lower()
        if _is_if_header(line):
            depth += 1
        elif lower == "end if":
            if depth == 0:
                return candidate
            depth -= 1
    raise RuntimeError(f"Could not find End If for line {index}.")


def _is_if_header(line: str) -> bool:
    lower = line.lower()
    return lower.startswith("if ") and lower.endswith("then")


def _is_else_if(line: str) -> bool:
    lower = " ".join(line.lower().split())
    return lower.startswith("else if ") and lower.endswith("then")


def _is_else(line: str) -> bool:
    return line.strip().lower() == "else"


def _if_condition(line: str) -> str:
    return re.sub(r"(?i)\s+then\s*$", "", line.strip()[3:]).strip()


def _else_if_condition(line: str) -> str:
    normalized = " ".join(line.strip().split())
    return re.sub(r"(?i)\s+then\s*$", "", normalized[8:]).strip()


def _call_name(line: str) -> str:
    text = line.split(None, 1)[1].strip()
    return text.split("(", 1)[0].strip()


def _strip_comment(line: str) -> str:
    in_string = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            in_string = not in_string
        if not in_string and line[index : index + 2] == "//":
            return line[:index]
        index += 1
    return line


def _split_args(text: str) -> list[str]:
    return [part.strip() for part in _split_unquoted(text, ",") if part.strip()]


def _split_unquoted(text: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_string = False
    for index, char in enumerate(text):
        if char == '"':
            in_string = not in_string
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == delimiter and depth == 0:
                parts.append(text[start:index])
                start = index + 1
    parts.append(text[start:])
    return parts


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _key_name(value: str) -> str:
    return _unquote(value.strip())
