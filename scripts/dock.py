"""Dock using continuous PD feedback with braking limits and an alignment gate."""

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.browser.simulator import (
    begin_episode_fast, disable_gpu_rendering, open_simulator, pause_simulation,
)
from src.evaluation.scenarios import (
    DEFAULT_SCENARIO, NEAR_DOCKING_SCENARIO, Scenario, apply_scenario,
    random_near_docking_scenario, random_scenario,
)
from src.recording import save_episode
from src.telemetry.state import READ_STATE


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_PATH = ROOT / "src/control/pd_controller.js"


def prepare_episode(page, scenario: Scenario, *, config: dict | None = None,
                    headed: bool = False) -> None:
    """Load, pause, and atomically arm a fresh simulator episode."""
    page.set_default_timeout(60000)
    page.set_default_navigation_timeout(60000)
    page.clock.install()
    # Playwright's clock belongs to the browser context, which a batch reuses.
    page.clock.resume()
    open_simulator(page)
    if not headed:
        disable_gpu_rendering(page)
    page.evaluate(f"window.__readDockingState = ({READ_STATE})")
    page.evaluate("config => { window.__pdConfig = config; }", config or {})
    page.evaluate(CONTROLLER_PATH.read_text())
    begin_episode_fast(page)
    pause_simulation(page)
    apply_scenario(page, scenario, arm_pd=True)


def run_episode(page, scenario: Scenario, output_path: Path, *, max_frames: int = 6000,
                max_seconds: float = 120, config: dict | None = None,
                headed: bool = False, fast: bool = False) -> dict:
    """Run a fresh page and save a frame-indexed action recording, even on timeout."""
    prepare_episode(page, scenario, config=config, headed=headed)
    print(f"start={scenario.name} seed={scenario.seed} budget={max_frames}", flush=True)
    started = time.monotonic()
    stop_reason = "FRAME_BUDGET"
    while True:
        snapshot = page.evaluate("""() => ({
            frames: window.__pdController.frames,
            result: window.__pdController.result,
        })""")
        if snapshot["result"] or snapshot["frames"] >= max_frames:
            break
        if time.monotonic() - started >= max_seconds:
            stop_reason = "WALL_TIME_LIMIT"
            break
        remaining = max_frames - snapshot["frames"]
        if fast:
            # The simulator integrates once per render, with no elapsed-time factor.
            # Keep its full render/physics/collision path; skip cosmetic timer waits.
            page.evaluate("""count => {
                for (let i = 0; i < count && !isGameOver; i++) render();
            }""", min(250, remaining))
        else:
            page.clock.run_for(min(1000, 16 * remaining))
    result = page.evaluate("window.__pdController.result")
    outcome = result["outcome"] if result else stop_reason
    data = save_episode(page, outcome, scenario, output_path)
    final = data["final"]
    summary = {
        "scenario": scenario.as_dict(), "outcome": outcome,
        "driver": "render" if fast else "clock",
        "frame": final["frame"], "range_m": final["range_m"],
        "lateral_m": (final["position_error_m"]["lateral_y"] ** 2 +
                      final["position_error_m"]["lateral_z"] ** 2) ** 0.5,
        "max_angle_deg": max(abs(value) for value in final["angle_deg"].values()),
        "max_velocity_m_per_frame": max(abs(value) for value in final["velocity_m_per_frame"].values()),
        "final_approach_start_frame": data["final_approach_start_frame"],
        "final_approach_frames": (final["frame"] - data["final_approach_start_frame"]
                                  if data["final_approach_start_frame"] is not None else None),
        "pulses": sum(len(record["actions"]) for record in data["records"]),
        "message": final["fail_message"],
        "recording": str(output_path),
    }
    print(json.dumps(summary), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--near", action="store_true", help="use a settled 6–10 m start")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--fast", action="store_true", help="step physics without cosmetic timer waits")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.max_seconds <= 0 or (args.max_frames is not None and args.max_frames <= 0):
        parser.error("time and frame budgets must be positive")
    factory = random_near_docking_scenario if args.near else random_scenario
    scenario = factory(args.seed) if args.seed is not None else (
        NEAR_DOCKING_SCENARIO if args.near else DEFAULT_SCENARIO)
    output = args.output or ROOT / "results" / f"pd_{scenario.name}_{args.seed}.json"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            summary = run_episode(page, scenario, output,
                                  max_frames=args.max_frames or (1500 if args.near else 6000),
                                  max_seconds=args.max_seconds, headed=args.headed, fast=args.fast)
            if args.headed:
                page.clock.resume()
                page.wait_for_timeout(3000)
            return 0 if summary["outcome"] == "SUCCESS" else 1
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
