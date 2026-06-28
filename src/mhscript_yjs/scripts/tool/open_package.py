from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from mhscript_yjs.core.config import ProjectConfig, load_config
from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import VK_ENTER
from mhscript_yjs.drivers.yjs import YjsDevice
from mhscript_yjs.runtime.logging import setup_script_logger
from mhscript_yjs.runtime.timing import NullSleeper, Sleeper
from mhscript_yjs.vision.matcher import TemplateMatcher
from mhscript_yjs.vision.screenshot import MssScreenCapture
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo, find_window


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
    ) -> None:
        self.config = config
        self.device = device
        self.matcher = matcher
        self.sleeper = sleeper
        self.logger = logger
        self.window_info = window_info
        self.groups = build_groups(config)
        self.next_after_confirm = 2
        self.no_find_count = 0

    def run(self, *, max_iterations: int | None = None) -> OpenPackageResult:
        window = self.window_info or find_window(self.config.maple_story.window_title)
        region = Region.from_bounds(window.x, window.y, window.right, window.bottom)
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
                if max_iterations is not None and iterations >= max_iterations:
                    return self._result("iteration_limit", iterations)
                iterations += 1

                self.logger.debug(
                    "loop_start iteration=%s no_find_count=%s next_after_confirm=%s",
                    iterations,
                    self.no_find_count,
                    self.next_after_confirm,
                )

                confirm = self.matcher.match_any(self.groups.confirm, region)
                if confirm:
                    self._handle_confirm(confirm, region)
                else:
                    jing = self.matcher.match_any(self.groups.jing, region)
                    if jing:
                        self._handle_jing(jing, region)
                    else:
                        shi = self.matcher.match_any(self.groups.shi, region)
                        if shi:
                            self._handle_shi(shi, region)
                        else:
                            self.no_find_count += 1
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
        finally:
            self.device.close()

    def _handle_confirm(self, match: MatchResult, region: Region) -> None:
        self.logger.info(
            "stage=confirm image=%s x=%s y=%s score=%.6f next_after_confirm=%s",
            match.image_path,
            match.x,
            match.y,
            match.score,
            self.next_after_confirm,
        )
        self.device.press_key(VK_ENTER, 1)
        self.sleeper.delay_random_ms(
            self.config.timing.confirm_delay_min_ms,
            self.config.timing.confirm_delay_max_ms,
        )
        self.no_find_count = 0

        expected = self.groups.shi if self.next_after_confirm == 2 else self.groups.jing
        matched = self._wait_for_group(expected, region)
        if matched:
            self._click_match(matched)
            self.no_find_count = 0
            self.next_after_confirm = 3 if self.next_after_confirm == 2 else 2
            self.logger.info("stage_after_confirm_switched next_after_confirm=%s", self.next_after_confirm)
        else:
            self.no_find_count += 1
            self.logger.info(
                "confirm_followup_not_found expected=%s no_find_count=%s",
                expected.name,
                self.no_find_count,
            )

    def _handle_jing(self, match: MatchResult, region: Region) -> None:
        self.logger.info(
            "stage=jing image=%s x=%s y=%s score=%.6f",
            match.image_path,
            match.x,
            match.y,
            match.score,
        )
        self._click_match(match)
        self.no_find_count = 0
        self.next_after_confirm = 2
        if self._wait_for_confirm(region):
            self.no_find_count = 0
        else:
            self.no_find_count += 1
            self.logger.info("jing_followup_confirm_not_found no_find_count=%s", self.no_find_count)

    def _handle_shi(self, match: MatchResult, region: Region) -> None:
        self.logger.info(
            "stage=shi image=%s x=%s y=%s score=%.6f",
            match.image_path,
            match.x,
            match.y,
            match.score,
        )
        self._click_match(match)
        self.no_find_count = 0
        self.next_after_confirm = 3
        if self._wait_for_confirm(region):
            self.no_find_count = 0
        else:
            self.no_find_count += 1
            self.logger.info("shi_followup_confirm_not_found no_find_count=%s", self.no_find_count)

    def _wait_for_confirm(self, region: Region) -> bool:
        matched = self._wait_for_group(self.groups.confirm, region)
        if not matched:
            return False
        self.logger.info(
            "followup_confirm image=%s x=%s y=%s score=%.6f",
            matched.image_path,
            matched.x,
            matched.y,
            matched.score,
        )
        self.device.press_key(VK_ENTER, 1)
        self.sleeper.delay_random_ms(
            self.config.timing.confirm_delay_min_ms,
            self.config.timing.confirm_delay_max_ms,
        )
        return True

    def _wait_for_group(self, group: ImageGroup, region: Region) -> MatchResult | None:
        for attempt in range(1, 11):
            match = self.matcher.match_any(group, region)
            if match:
                self.logger.debug("wait_group_found group=%s attempt=%s", group.name, attempt)
                return match
            self.logger.debug("wait_group_pending group=%s attempt=%s", group.name, attempt)
            self.sleeper.delay_ms(self.config.timing.poll_interval_ms)
        return None

    def _click_match(self, match: MatchResult) -> None:
        x = match.x + self.config.open_package.click_offset_x
        y = match.y
        self.logger.info(
            "click_match group=%s image=%s move_to=(%s,%s) source=(%s,%s) offset_x=%s",
            match.group,
            match.image_path,
            x,
            y,
            match.x,
            match.y,
            self.config.open_package.click_offset_x,
        )
        self.device.move_to(x, y, smooth=True)
        self.sleeper.delay_ms(self.config.timing.post_move_delay_ms)
        self.device.left_click(1)
        self.sleeper.delay_random_ms(
            self.config.timing.confirm_delay_min_ms,
            self.config.timing.confirm_delay_max_ms,
        )

    def _result(self, exit_reason: str, iterations: int) -> OpenPackageResult:
        result = OpenPackageResult(
            exit_reason=exit_reason,
            iterations=iterations,
            no_find_count=self.no_find_count,
        )
        self.logger.info("open_package_exit %s", result)
        return result


def build_groups(config: ProjectConfig) -> OpenPackageGroups:
    image_root = config.maple_story.image_root
    settings = config.open_package
    return OpenPackageGroups(
        confirm=_group("confirm", image_root, settings.confirm_images, settings.match_threshold),
        jing=_group("jing", image_root, settings.jing_images, settings.match_threshold),
        shi=_group("shi", image_root, settings.shi_images, settings.match_threshold),
    )


def _group(name: str, image_root: Path, paths: tuple[str, ...], threshold: float) -> ImageGroup:
    return ImageGroup(
        name=name,
        paths=tuple(image_root / path for path in paths),
        threshold=threshold,
    )


def create_runner(
    *,
    config: ProjectConfig,
    dry_run: bool,
    skip_delays: bool,
    logger: logging.Logger,
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
    device: InputDevice = DryRunDevice(logger=logger) if dry_run else YjsDevice(config.yjs, logger=logger)
    sleeper: Sleeper = NullSleeper(logger=logger) if skip_delays else Sleeper(logger=logger)
    return OpenPackageRunner(
        config=config,
        device=device,
        matcher=matcher,
        sleeper=sleeper,
        logger=logger,
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
    return 0 if result.exit_reason in {"no_find_limit", "iteration_limit", "keyboard_interrupt"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
