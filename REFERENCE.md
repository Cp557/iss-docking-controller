# Technical Reference

Canonical description of the PD controller, simulator integration, and recorded evidence.

## System

Python launches the [SpaceX ISS Docking Simulator](https://iss-sim.spacex.com/) through Playwright. An in-page JavaScript controller runs once before each native `render()` physics step, reads structured simulator state, computes continuous feedback and alignment limits, and calls the simulator's own thruster controls.

```text
Simulator state → PD feedback + braking envelope + alignment gate → thruster pulses
       ↑                                                               ↓
       └──────────────── native physics and collision checks ────────────┘
```

The simulator integrates per render rather than by elapsed seconds. Translation changes `motionVector` by 0.001 m/frame per pulse at native precision, and velocity persists. All controller gains therefore use physics frames, meters, and radians. No derivatives are estimated using wall-clock time.

Rotation buttons increment or decrement `targetRotationX/Y/Z` by 0.5 degrees. Each frame the simulator updates angular rate as `rate += (-0.01 * target - rate) * 0.05`, then rotates the craft by that rate. Rotation buttons are not direct torque commands.

The simulator's optional gravity subtracts 0.0001 m from world vertical position each frame without modifying `motionVector`. The PD derivative uses the resulting physical velocity (`motionVector.y - gravity` when enabled), avoiding a steady vertical tracking bias without integral action.

## Translation PD

For each world axis, with `error = position - target`:

```text
u = clamp(-Kp * error, -Kd * speed_limit, Kd * speed_limit) - Kd * velocity
Kp = 0.00048
Kd = 0.04
```

The unsaturated law is proportional position feedback plus measured-velocity damping. Limiting the proportional term bounds the desired steady velocity. The world control vector is rotated into the craft's local frame with the inverse camera quaternion, then converted into positive/negative thruster pulses. There is no integral accumulator. A local effort deadband of `Kd * translationPulseSize * 0.45` suppresses tiny corrections; the actuator remains discrete rather than applying a continuously variable acceleration.

All position targets are the docking port at zero error. Lateral and attitude correction run continuously, including while approach is gated. There is no intermediate position target or mandatory stop at 8 m.

A single distance-based braking envelope applies from the start through docking:

```text
braking_limit = sqrt(0.0025^2 + 2 * 0.0006 * max(0, approach_distance - 0.3))
lateral_speed_limit = min(0.04, braking_limit)
approach_speed_limit = aligned ? min(0.2, braking_limit) : 0
```

The limits are in m/frame. When alignment fails, a zero approach limit removes the approach proportional term, leaving velocity damping to brake the craft. This commands a stop rather than instantaneously setting velocity to zero. No hold-position memory, phase transitions, or settling counters are used.

The final proportional term slows the craft further as it crosses the simulator's 0.2 m true-range capture boundary. The braking acceleration is deliberately below the nominal 0.001 m/frame velocity change from a pulse, leaving room for discrete actuation and tracking error. It is a conservative control parameter, not a proof of collision avoidance from arbitrary states.

## Attitude PD

The controller computes the shortest quaternion rotation vector relative to the port's identity orientation. This provides body-axis orientation error without treating coupled Euler angles as independent body rotations. Euler angles are retained for the simulator's alignment and success checks.

```text
rotation_target = clamp(2 * orientation_error + 50 * angular_rate, -0.1, 0.1)
```

The sign accounts for the simulator's negative mapping from rotation target to angular rate. Buttons track this target with a half-pulse deadband. The measured rate term supplies damping on top of the simulator's internal actuator dynamics. Gain values should not be transferred to a torque-driven spacecraft model.

## Alignment gate

Forward approach is enabled only while all of these conditions hold:

- Each Euler attitude error is at most 0.15 degrees.
- Each angular rate is at most 0.00001 rad/frame.
- Each native rotation target is below half a pulse.
- Lateral offset magnitude is at most 0.08 m.
- Lateral physical speed magnitude is at most 0.002 m/frame.

The controller evaluates the gate afresh every frame. If alignment is disturbed, it brakes the approach while continuing attitude and lateral correction. The approach resumes as soon as the same conditions hold again. The gate does not store a mode or wait for a fixed number of settled frames. These limits leave margin inside the native success tolerances, but braking authority remains finite; recovery from arbitrary unsafe near-port disturbances is not guaranteed.

`finalApproachStartFrame` is latched on the first frame with approach distance at most 8 m. It is evaluation bookkeeping only: crossing 8 m changes no control law, target, or limit. The timestamp never resets if the craft brakes, moves outward, or loses alignment.

## Scenarios and evaluation

`src/evaluation/scenarios.py` preserves the original native and seeded starts. The default is 200 m approach error, 12/30 m lateral errors, and 15/-20/-10 degree roll/pitch/yaw. Full-distance seeded starts sample 120–250 m approach error, ±30 m lateral offsets, and ±25 degree attitude. Near-port starts sample 6–10 m range, ±0.04 m lateral errors, and ±0.1 degree attitude.

Optional scenario fields add raw `motionVector` components in m/frame and initial roll/pitch/yaw rates in degrees/frame. Their defaults are zero, so old recordings and scenarios retain their meaning.

`python -m scripts.evaluate --suite existing` runs both fixed starts, full-distance seeds 5–16, and near-port seeds 15–34 (34 cases). `--suite holdout` runs full-distance and near-port seeds 35–54 (40 cases). `--suite stress` runs full-distance seeds 100–119 with initial approach velocities within ±0.04 m/frame, lateral velocities within ±0.02 m/frame, and angular rates within ±0.03 degrees/frame (20 cases). `--suite all` runs all 94 cases.

A pass requires the simulator's declared success, true range below 0.2 m, each attitude error at most 0.2 degrees, each raw velocity component at most 0.004 m/frame, and docking within 1,500 frames of first entry within 8 m approach distance. Full runs also have a 6,000-frame budget; near-port runs have a 1,500-frame total budget. Wall-time and frame limits produce a failure recording. Episode wall-time budgets begin after simulator startup; navigation and asset initialization have separate 60-second timeouts. Browser/network setup errors are counted as failures rather than skipped. Each summary is checkpointed after every case.

Evaluation normally calls the full native `render()` function under a paused clock, retaining physics, transforms, and raycast collision checks while skipping cosmetic timer waits. `--clock` advances animation timers instead. Both modes disable WebGL drawing, shader compilation, CSS labels, and thruster HUD animations; these do not alter the craft's dynamics or collision rules. Headed docking retains normal rendering. Simulator startup completes the native intro and warp timelines before the scenario is atomically applied.

Playwright's clock is context-scoped. The batch runner resumes it before loading each fresh page and pauses relative to the page's current virtual time, preventing a previous episode's paused or advanced clock from stalling the next episode. Docking, replay, and video rendering share `pause_simulation`, which schedules the pause 60 virtual seconds ahead to accommodate dispatch delays under parallel browser load. This is a clock jump before scenario reset and controller arming, not a real-time wait; episode frame and wall-time budgets start afterward.

`python -m scripts.check_controller` separately injects a 5 degree pitch disturbance and a 0.5 m lateral disturbance while the near-port craft is closing at more than 0.01 m/frame. Each check verifies immediate gate closure, a zero approach speed limit, braking, gate reopening, successful docking, and preservation of the original final-approach deadline. It also verifies an exact 50-frame limit and a wall-time timeout. The two limit cases must fail as requested and save their partial recordings; they are not counted as successful docking scenarios.

## Recording and replay

Recordings save controller identity/configuration, continuous guidance identity, scenario, the first 8 m final-approach timestamp, frame-indexed actions, simulator outcome, pre-physics capture telemetry, and the exact post-physics game-over state. Saving the state at the game-over transition prevents the success animation from changing the recorded final position.

`python -m scripts.replay --fast --fast-start` replays `demo/pd-recording.json` by default, without installing the controller. It applies each saved action through the simulator controls using the animation clock, checks the declared outcome, compares the pre-capture telemetry, and for PD recordings also compares the full game-over state with a 0.001 numeric tolerance. Frame counting stops when the simulator declares game over. Replay accepts PD recordings through `--recording`.

`python -m scripts.render_demo --trace demo/pd-recording.json --fps 24 --stride 5 --output results/pd-video` renders the simulator view without a custom information overlay. Video rendering verifies capture telemetry before showing the success screen. The README embeds the GitHub-hosted video from the [demo issue](https://github.com/Cp557/iss-docking/issues/2). Generated MP4/GIF files remain local and are ignored by Git; the published video and GIF play at 1.5× speed relative to a nominal 60-frame/s simulator (90 simulator frames per playback second). The source was captured at 24 fps with one image per five simulator frames, then its docking timestamps were stretched by 4/3. The native success animation is also retimed to 1.5×, followed by a two-second final hold. The published MP4 remains 24 fps.

## Evidence

On October 20, 2026, the continuous PD controller docked on **94/94** evaluated starts: 13 existing full-distance starts, 21 existing near-port starts, 20 held-out full-distance starts, 20 held-out near-port starts, and 20 starts with initial velocity/rate disturbances. The scenario inputs are identical to the previous 94-case validation.

Each completed case met the native simulator tolerances and the same total frame budgets. The final-approach deadline is now measured from first entry within 8 m rather than from a controller phase transition. The largest final attitude error was 0.12512 degrees, the largest final raw velocity component was 0.002964 m/frame, and the longest final approach took 355 frames.

Three initial cases failed before controller arming because the browser clock overtook a one-second pause target under parallel load. The shared pause helper with a 60-second virtual dispatch margin fixed that setup race; those exact cases were rerun successfully. Initial errors and their successful reruns remain in the validation artifacts.

The 5 degree pitch-disturbance recovery test docked at frame 606 after 266 frames with the approach gate closed. The 0.5 m lateral-disturbance test docked at frame 510 after 164 frames with the gate closed. Both disturbances were injected while the craft was approaching faster than 0.01 m/frame near 3.5 m. Each test verified braking and gate reopening; the original final-approach timestamp stayed at frame 0. The 50-frame limit saved exactly 50 action records, and the wall-time limit saved a timeout recording. Two recording regression unit tests and Python syntax checks passed.

The default run docks at frame 2,419, with 316 final-approach frames. The previous controller required 3,661 total frames. Running the installed continuous controller through the animation clock and through direct render stepping produced identical actions, pre-capture telemetry, and final state. Its new PD action recording also replayed with zero numeric state difference.

Evaluation results are recorded in `demo/pd-results.json`, including the controller source hash, configuration, per-case metrics, and recovery evidence. The representative successful action trace is `demo/pd-recording.json`. Detailed traces are retained under ignored `results/continuous-pd-validation/`.

These are browser-simulator experiments. The tested ranges, initial conditions, actuator model, and fixed target do not establish robustness to arbitrary disturbances, sensor noise, changing simulator versions, or real spacecraft dynamics.

## Files

```text
src/control/pd_controller.js     Continuous PD, braking limits, and alignment gate
scripts/dock.py                Launch a single PD docking episode
scripts/evaluate.py            Original, held-out, and disturbed scenario suites
scripts/check_controller.py    Live recovery and timeout regression checks
scripts/replay.py              Verify recorded simulator actions
scripts/render_demo.py         Render a replay video
src/evaluation/scenarios.py    Fixed, seeded, and optionally disturbed starts
src/browser/simulator.py       Simulator startup and headless visual suppression
src/telemetry/state.py         Structured simulator telemetry
src/recording.py               Episode recording with exact game-over state
demo/pd-recording.json         Representative successful PD trace
demo/pd-results.json           PD evaluation summaries
results/                      Generated detailed traces, ignored by Git
```
