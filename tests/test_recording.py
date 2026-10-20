"""Protect recordings from the simulator's post-game camera animation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.evaluation.scenarios import DEFAULT_SCENARIO
from src.recording import save_episode


class RecordingTests(unittest.TestCase):
    def snapshot(self, final):
        return {
            "trace": [{"frame": 0, "actions": ["translateForward"]}],
            "result": {"outcome": "SUCCESS"} if final else None,
            "final": final,
            "config": {"positionKp": 0.00048, "positionKd": 0.04},
            "final_approach_start_frame": 0,
        }

    def test_success_uses_captured_state_without_reading_animated_camera(self):
        captured = {"frame": 1, "range_m": 0.199, "game_over": True}
        page = Mock()
        # Any second evaluation would read the already-animating camera.
        page.evaluate.side_effect = [self.snapshot(captured)]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "episode.json"
            save_episode(page, "SUCCESS", DEFAULT_SCENARIO, output)
            recorded = json.loads(output.read_text())
        self.assertEqual(recorded["final"], captured)
        self.assertEqual(page.evaluate.call_count, 1)

    def test_timeout_saves_live_state_when_no_game_over_was_captured(self):
        live = {"frame": 50, "range_m": 200, "game_over": False}
        page = Mock()
        page.evaluate.side_effect = [self.snapshot(None), live]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "episode.json"
            save_episode(page, "FRAME_BUDGET", DEFAULT_SCENARIO, output)
            recorded = json.loads(output.read_text())
        self.assertEqual(recorded["final"], live)
        self.assertEqual(recorded["outcome"], "FRAME_BUDGET")
        self.assertIsNone(recorded["result"])


if __name__ == "__main__":
    unittest.main()
