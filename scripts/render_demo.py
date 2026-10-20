"""Render a video from a recorded docking run."""

import argparse
import json
import math
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.replay import INSTALL_REPLAY, max_capture_difference
from src.telemetry.state import READ_STATE
from src.browser.simulator import begin_episode_fast, open_simulator, pause_simulation
from src.evaluation.scenarios import Scenario, apply_scenario


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = ROOT / "demo/pd-recording.json"
DEFAULT_OUTPUT = ROOT / "demo"
START_FRAME = 0
PREVIEW_FRAMES = (START_FRAME, 180, 530, 1590, 2300, 2400, 3000, 3300)
SUCCESS_TRANSITION_MS = 4000
SUCCESS_FRAME_INTERVAL_MS = 250
DRAW_ON_DEMAND = """() => {
    const drawScene = renderer.render.bind(renderer);
    const drawNavball = navballRenderer.render.bind(navballRenderer);
    let sceneArgs;
    let navballArgs;
    renderer.render = (...args) => { sceneArgs = args; };
    navballRenderer.render = (...args) => { navballArgs = args; };
    window.__demoDrawFrame = () => {
        if (sceneArgs) drawScene(...sceneArgs);
        if (navballArgs) drawNavball(...navballArgs);
    };
}"""


def advance_to_frame(page, target: int) -> int:
    while True:
        snapshot = page.evaluate("""() => ({
            frame: window.__dockingFrame, ended: window.__replayResult !== null,
        })""")
        frame = snapshot["frame"]
        if frame >= target:
            return frame
        if snapshot["ended"]:
            raise ValueError(f"Replay ended at frame {frame}, before requested frame {target}")
        remaining = target - frame
        page.clock.run_for(1000 if remaining > 65 else (100 if remaining > 7 else 17))


def render(trace_path: Path, output: Path, preview: bool, fps: int, stride: int) -> None:
    data = json.loads(trace_path.read_text())
    if data.get("controller") != "pd" or data["outcome"] != "SUCCESS" or not data.get("result"):
        raise ValueError("Demo requires a recorded successful PD docking")
    output.mkdir(parents=True, exist_ok=True)
    target_frame = data["final"]["frame"]
    final_visual_frame = target_frame - 1
    targets = (
        sorted({frame for frame in PREVIEW_FRAMES if frame < final_visual_frame}
               | {final_visual_frame})
        if preview else list(range(START_FRAME, final_visual_frame, stride))
        + [final_visual_frame]
    )
    events = [{"frame": record["frame"], "actions": record["actions"]}
              for record in data["records"]]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        try:
            page.clock.install()
            open_simulator(page)
            begin_episode_fast(page)
            pause_simulation(page)
            page.evaluate(f"window.__readDockingState = ({READ_STATE})")
            page.evaluate(INSTALL_REPLAY, events)
            apply_scenario(page, Scenario(**data["scenario"]))
            page.evaluate(DRAW_ON_DEMAND)
            page.evaluate("window.__dockingFrame = 0; window.__replayArmed = true")

            for index, target in enumerate(targets):
                frame = advance_to_frame(page, target)
                page.evaluate("window.__demoDrawFrame()")
                name = f"preview_{frame:04d}.png" if preview else f"frame_{index:04d}.png"
                page.screenshot(path=str(output / name))
                if index % 25 == 0 or index == len(targets) - 1:
                    print(f"captured={index + 1}/{len(targets)} simulator_frame={frame}", flush=True)

            advance_to_frame(page, target_frame)
            replay_result = page.evaluate("window.__replayResult")
            difference = (
                max_capture_difference(replay_result["preTelemetry"], data["result"]["telemetry"])
                if replay_result else math.inf
            )
            if (replay_result is None or replay_result["outcome"] != "SUCCESS"
                    or not math.isclose(difference, 0, abs_tol=1e-3)):
                raise ValueError(f"Demo replay diverged from recorded success: {difference}")
            print(f"replay_match=True max_capture_difference={difference:.6f}", flush=True)

            transition_steps = range(0, SUCCESS_TRANSITION_MS + 1,
                                     SUCCESS_FRAME_INTERVAL_MS)
            for index, elapsed_ms in enumerate(transition_steps):
                if index:
                    page.clock.run_for(SUCCESS_FRAME_INTERVAL_MS)
                page.evaluate("window.__demoDrawFrame()")
                if not preview or elapsed_ms == SUCCESS_TRANSITION_MS:
                    name = ("preview_success.png" if preview
                            else f"frame_{len(targets) + index:04d}.png")
                    page.screenshot(path=str(output / name))
            if not page.get_by_text("SUCCESS", exact=True).is_visible():
                raise ValueError("Simulator success screen did not appear")
            print("success_screen_captured=True", flush=True)
        finally:
            browser.close()

    if preview:
        return
    video_path = output / "docking.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
         "-i", str(output / "frame_%04d.png"), "-vf", "tpad=stop_mode=clone:stop_duration=2",
         "-c:v", "libx264", "-crf", "23",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_path)],
        check=True,
    )
    for pattern in ("frame_*.png", "frame_*.jpg"):
        for frame_path in output.glob(pattern):
            frame_path.unlink()
    print(f"video={video_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview", action="store_true", help="render review stills")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--stride", type=int, default=14)
    args = parser.parse_args()
    if args.fps <= 0 or args.stride <= 0:
        parser.error("fps and stride must be positive")
    render(args.trace, args.output, args.preview, args.fps, args.stride)


if __name__ == "__main__":
    main()
