from __future__ import annotations

from dataclasses import dataclass

from mhscript_yjs.runtime.control import RunControl, StopRequested
from mhscript_yjs.runtime.timing import Sleeper


@dataclass(frozen=True)
class PlaceholderResult:
    exit_reason: str
    iterations: int


def run_placeholder_script(
    *,
    display_name: str,
    control: RunControl,
    sleeper: Sleeper,
    iterations: int = 6,
) -> PlaceholderResult:
    sleeper.logger.info("%s 目前是占位脚本，开始执行占位流程。", display_name)
    completed = 0
    try:
        for step in range(1, iterations + 1):
            control.wait_if_paused()
            if control.stop_requested():
                raise StopRequested("stop requested")
            sleeper.logger.info("%s 占位脚本第 %s/%s 次循环。", display_name, step, iterations)
            completed = step
            sleeper.delay_ms(350)
    except StopRequested:
        sleeper.logger.info("%s 占位脚本收到停止请求。", display_name)
        return PlaceholderResult(exit_reason="stop_requested", iterations=completed)

    sleeper.logger.info("%s 占位脚本执行结束。", display_name)
    return PlaceholderResult(exit_reason="placeholder_complete", iterations=completed)
