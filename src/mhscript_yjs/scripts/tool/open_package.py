from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from mhscript_yjs.core.config import ProjectConfig, load_config
from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.controlled import ControlledInputDevice
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import VK_ENTER
from mhscript_yjs.drivers.yjs import YjsDevice
from mhscript_yjs.runtime.control import NullRunControl, RunControl, StopRequested
from mhscript_yjs.runtime.logging import log_important, setup_script_logger
from mhscript_yjs.runtime.timing import NullSleeper, Sleeper
from mhscript_yjs.vision.matcher import TemplateMatcher
from mhscript_yjs.vision.screenshot import MssScreenCapture
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo, find_window, refresh_window_info


@dataclass(frozen=True)
class OpenPackageGroups:
    confirm: ImageGroup
    jing: ImageGroup
    shi: ImageGroup


@dataclass(frozen=True)
class OpenPackageResult:
    exit_reason: str
    iterations: int
    no_find_count: int
    cards_opened: int


class OpenPackageRunner:
    def __init__(
        self,
        *,
        config: ProjectConfig,
        device: InputDevice,
        matcher: TemplateMatcher,
        sleeper: Sleeper,
        logger: logging.Logger,
        window_info: WindowInfo | None = None,
        control: RunControl | None = None,
    ) -> None:
        self.config = config
        self.device = device
        self.matcher = matcher
        self.sleeper = sleeper
        self.logger = logger
        self.window_info = window_info
        self._dynamic_window = window_info is None
        self.control = control or NullRunControl()
        self.groups = build_groups(config)
        self.next_after_confirm = 2
        self.no_find_count = 0
        self.cards_opened = 0

    def run(self, *, max_iterations: int | None = None) -> OpenPackageResult:
        window = self.window_info or find_window(self.config.maple_story.window_title)
        self.window_info = window
        region = _window_region(window)
        self.logger.info(
            "自动开包开始：窗口=%r，客户区=(%s,%s %sx%s)。",
            window.title,
            window.x,
            window.y,
            window.width,
            window.height,
        )
        self.logger.info(
            "open_package_start hwnd=%s title=%r client=(%s,%s %sx%s) region=%s",
            window.hwnd,
            window.title,
            window.x,
            window.y,
            window.width,
            window.height,
            region,
        )

        iterations = 0
        try:
            self.device.open()
            while True:
                self._checkpoint()
                if max_iterations is not None and iterations >= max_iterations:
                    return self._result("iteration_limit", iterations)
                iterations += 1

                self.logger.debug(
                    "自动开包第 %s 次循环：连续未识别 %s 次，确认后目标=%s。",
                    iterations,
                    self.no_find_count,
                    "石" if self.next_after_confirm == 2 else "精",
                )
                self.logger.debug(
                    "loop_start iteration=%s no_find_count=%s next_after_confirm=%s",
                    iterations,
                    self.no_find_count,
                    self.next_after_confirm,
                )
                window = self._current_window()
                region = _window_region(window)

                confirm = self._match_any(self.groups.confirm, region)
                if confirm:
                    self._handle_confirm(confirm, region)
                else:
                    jing = self._match_any(self.groups.jing, region)
                    if jing:
                        self._handle_jing(jing, region)
                    else:
                        shi = self._match_any(self.groups.shi, region)
                        if shi:
                            self._handle_shi(shi, region)
                        else:
                            self.no_find_count += 1
                            self.logger.debug(
                                "第 %s 次循环未识别到目标，连续未识别 %s 次。",
                                iterations,
                                self.no_find_count,
                            )
                            self.logger.debug(
                                "no_match iteration=%s no_find_count=%s",
                                iterations,
                                self.no_find_count,
                            )
                            self.sleeper.delay_ms(self.config.timing.poll_interval_ms)

                if self.no_find_count >= self.config.open_package.no_find_limit:
                    return self._result("no_find_limit", iterations)
        except KeyboardInterrupt:
            self.logger.warning("interrupted_by_user iterations=%s", iterations)
            return self._result("keyboard_interrupt", iterations)
        except StopRequested:
            self.logger.info("stop_requested iterations=%s", iterations)
            return self._result("stop_requested", iterations)
        finally:
            self.device.close()

    def _handle_confirm(self, match: MatchResult, region: Region) -> None:
        self._checkpoint()
        self.logger.debug(
            "发现确认按钮：图片=%s，坐标=(%s,%s)，相似度=%.6f。",
            match.image_path,
            match.x,
            match.y,
            match.score,
        )
        self.logger.debug(
            "stage=confirm image=%s x=%s y=%s score=%.6f next_after_confirm=%s",
            match.image_path,
            match.x,
            match.y,
            match.score,
            self.next_after_confirm,
        )
        self._checkpoint()
        self.device.press_key(VK_ENTER, 1)
        self.logger.debug("已按下 Enter 确认。")
        self.sleeper.delay_random_ms(
            self.config.timing.confirm_delay_min_ms,
            self.config.timing.confirm_delay_max_ms,
        )
        self.no_find_count = 0
        self._record_confirmed_action(self.next_after_confirm)

        expected = self.groups.shi if self.next_after_confirm == 2 else self.groups.jing
        matched = self._wait_for_group(expected, region)
        if matched:
            self._click_match(matched)
            self.no_find_count = 0
            self.next_after_confirm = 3 if self.next_after_confirm == 2 else 2
            self.logger.debug(
                "确认后的目标已切换：下次确认后优先处理%s。",
                "石" if self.next_after_confirm == 2 else "精",
            )
            self.logger.debug(
                "stage_after_confirm_switched next_after_confirm=%s",
                self.next_after_confirm,
            )
        else:
            self.no_find_count += 1
            self.logger.debug(
                "确认后的目标未找到：期望=%s，连续未识别 %s 次。",
                expected.name,
                self.no_find_count,
            )
            self.logger.debug(
                "confirm_followup_not_found expected=%s no_find_count=%s",
                expected.name,
                self.no_find_count,
            )

    def _handle_jing(self, match: MatchResult, region: Region) -> None:
        self._checkpoint()
        self.logger.debug(
            "发现“精”目标：图片=%s，坐标=(%s,%s)，相似度=%.6f。",
            match.image_path,
            match.x,
            match.y,
            match.score,
        )
        self.logger.debug(
            "stage=jing image=%s x=%s y=%s score=%.6f",
            match.image_path,
            match.x,
            match.y,
            match.score,
        )
        self._click_match(match)
        self.no_find_count = 0
        self.next_after_confirm = 2
        if self._wait_for_confirm(region, confirmed_action=self.next_after_confirm):
            self.no_find_count = 0
        else:
            self.no_find_count += 1
            self.logger.debug("点击“精”后未找到确认按钮，连续未识别 %s 次。", self.no_find_count)
            self.logger.debug("jing_followup_confirm_not_found no_find_count=%s", self.no_find_count)

    def _handle_shi(self, match: MatchResult, region: Region) -> None:
        self._checkpoint()
        self.logger.debug(
            "发现“石”目标：图片=%s，坐标=(%s,%s)，相似度=%.6f。",
            match.image_path,
            match.x,
            match.y,
            match.score,
        )
        self.logger.debug(
            "stage=shi image=%s x=%s y=%s score=%.6f",
            match.image_path,
            match.x,
            match.y,
            match.score,
        )
        self._click_match(match)
        self.no_find_count = 0
        self.next_after_confirm = 3
        if self._wait_for_confirm(region, confirmed_action=self.next_after_confirm):
            self.no_find_count = 0
        else:
            self.no_find_count += 1
            self.logger.debug("点击“石”后未找到确认按钮，连续未识别 %s 次。", self.no_find_count)
            self.logger.debug("shi_followup_confirm_not_found no_find_count=%s", self.no_find_count)

    def _wait_for_confirm(self, region: Region, *, confirmed_action: int) -> bool:
        matched = self._wait_for_group(self.groups.confirm, region)
        if not matched:
            return False
        self.logger.debug(
            "followup_confirm image=%s x=%s y=%s score=%.6f",
            matched.image_path,
            matched.x,
            matched.y,
            matched.score,
        )
        self._checkpoint()
        self.device.press_key(VK_ENTER, 1)
        self.logger.debug("已按下后续确认 Enter。")
        self.sleeper.delay_random_ms(
            self.config.timing.confirm_delay_min_ms,
            self.config.timing.confirm_delay_max_ms,
        )
        self._record_confirmed_action(confirmed_action)
        return True

    def _record_confirmed_action(self, confirmed_action: int) -> None:
        if confirmed_action == 2:
            log_important(self.logger, "将卡牌转成精华")
            return

        if confirmed_action == 3:
            self.cards_opened += 10
            log_important(self.logger, "开包获取10张卡牌")
            log_important(self.logger, "当前总共开了 %s 张卡牌", self.cards_opened)

    def _wait_for_group(self, group: ImageGroup, region: Region) -> MatchResult | None:
        for attempt in range(1, 11):
            self._checkpoint()
            match = self._match_any(group, region)
            if match:
                self.logger.debug("wait_group_found group=%s attempt=%s", group.name, attempt)
                return match
            self.logger.debug("wait_group_pending group=%s attempt=%s", group.name, attempt)
            self.sleeper.delay_ms(self.config.timing.poll_interval_ms)
        return None

    def _click_match(self, match: MatchResult) -> None:
        self._checkpoint()
        x = match.x + self.config.open_package.click_offset_x
        y = match.y
        self.logger.debug(
            "准备点击目标：分组=%s，移动到=(%s,%s)，原始坐标=(%s,%s)。",
            match.group,
            x,
            y,
            match.x,
            match.y,
        )
        self.logger.debug(
            "click_match group=%s image=%s move_to=(%s,%s) source=(%s,%s) offset_x=%s",
            match.group,
            match.image_path,
            x,
            y,
            match.x,
            match.y,
            self.config.open_package.click_offset_x,
        )
        self._checkpoint()
        self.device.move_to(x, y, smooth=True)
        self.sleeper.delay_ms(self.config.timing.post_move_delay_ms)
        self._checkpoint()
        self.device.left_click(1)
        self.logger.debug("已完成目标点击。")
        self.sleeper.delay_random_ms(
            self.config.timing.confirm_delay_min_ms,
            self.config.timing.confirm_delay_max_ms,
        )

    def _result(self, exit_reason: str, iterations: int) -> OpenPackageResult:
        result = OpenPackageResult(
            exit_reason=exit_reason,
            iterations=iterations,
            no_find_count=self.no_find_count,
            cards_opened=self.cards_opened,
        )
        log_important(
            self.logger,
            "自动开包结束：原因=%s，循环次数=%s，连续未识别=%s，总共开了 %s 张卡牌。",
            result.exit_reason,
            result.iterations,
            result.no_find_count,
            result.cards_opened,
        )
        self.logger.info("open_package_exit %s", result)
        return result

    def _match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self._checkpoint()
        return self.matcher.match_any(group, region)

    def _current_window(self) -> WindowInfo:
        if not self._dynamic_window:
            if self.window_info is None:
                self.window_info = find_window(self.config.maple_story.window_title)
            return self.window_info
        previous = self.window_info
        window = refresh_window_info(previous, self.config.maple_story.window_title)
        if previous is None or (
            previous.x,
            previous.y,
            previous.width,
            previous.height,
        ) != (window.x, window.y, window.width, window.height):
            self.logger.info(
                "open_package_window_refreshed hwnd=%s client=(%s,%s %sx%s)",
                window.hwnd,
                window.x,
                window.y,
                window.width,
                window.height,
            )
        self.window_info = window
        return window

    def _checkpoint(self) -> None:
        self.control.wait_if_paused()
        if self.control.stop_requested():
            raise StopRequested("stop requested")


def build_groups(config: ProjectConfig) -> OpenPackageGroups:
    image_root = config.maple_story.image_root
    settings = config.open_package
    return OpenPackageGroups(
        confirm=_group(
            "confirm",
            image_root,
            settings.confirm_images,
            settings.confirm_match_threshold,
        ),
        jing=_group("jing", image_root, settings.jing_images, settings.event_match_threshold),
        shi=_group("shi", image_root, settings.shi_images, settings.event_match_threshold),
    )


def _group(name: str, image_root: Path, paths: tuple[str, ...], threshold: float) -> ImageGroup:
    return ImageGroup(
        name=name,
        paths=tuple(image_root / path for path in paths),
        threshold=threshold,
    )


def _window_region(window: WindowInfo) -> Region:
    return Region.from_bounds(window.x, window.y, window.right, window.bottom)


def create_runner(
    *,
    config: ProjectConfig,
    dry_run: bool,
    skip_delays: bool,
    logger: logging.Logger,
    control: RunControl | None = None,
) -> OpenPackageRunner:
    try:
        capture = MssScreenCapture()
    except ModuleNotFoundError as exc:
        missing = exc.name or str(exc)
        raise RuntimeError(
            f"Missing runtime dependency {missing!r}. "
            "Install project dependencies first, for example: "
            "python -m pip install -e ."
        ) from exc
    matcher = TemplateMatcher(capture=capture, logger=logger)
    raw_device: InputDevice = (
        DryRunDevice(logger=logger) if dry_run else YjsDevice(config.yjs, logger=logger)
    )
    run_control = control or NullRunControl()
    device: InputDevice = ControlledInputDevice(raw_device, run_control)
    sleeper: Sleeper = (
        NullSleeper(logger=logger, control=run_control)
        if skip_delays
        else Sleeper(logger=logger, control=run_control)
    )
    return OpenPackageRunner(
        config=config,
        device=device,
        matcher=matcher,
        sleeper=sleeper,
        logger=logger,
        control=run_control,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Python migration of Tool/open_package.km")
    parser.add_argument("--config", type=Path, default=None, help="Path to a TOML config file")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Control the YiJianShu device. Default is dry-run logging only.",
    )
    parser.add_argument(
        "--skip-delays",
        action="store_true",
        help="Skip sleeps. Useful for dry-run diagnostics and tests.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Stop after N main-loop iterations.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    logger = setup_script_logger(
        script_name="open_package",
        log_dir=config.app.log_dir,
        level=config.app.log_level,
    )
    logger.info(
        "mode=%s config=%s image_root=%s dll_path=%s source_km=%s",
        "live" if args.live else "dry-run",
        args.config or config.project_root / "config" / "default.toml",
        config.maple_story.image_root,
        config.yjs.dll_path,
        config.open_package.source_km,
    )
    runner = create_runner(
        config=config,
        dry_run=not args.live,
        skip_delays=args.skip_delays,
        logger=logger,
    )
    result = runner.run(max_iterations=args.max_iterations)
    return 0 if result.exit_reason in {
        "no_find_limit",
        "iteration_limit",
        "keyboard_interrupt",
        "stop_requested",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
