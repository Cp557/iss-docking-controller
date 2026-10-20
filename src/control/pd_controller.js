// All gains use physics frames, meters, and radians; no wall-clock derivatives.
(function () {
    if (window.__pdController) throw new Error("PD controller already installed");

    const DEG = 180 / Math.PI;
    const config = {
        positionKp: 0.00048,
        positionKd: 0.04,
        attitudeKp: 2,
        attitudeKd: 50,
        maxRotationTarget: 0.1,
        approachSpeed: 0.2,
        lateralSpeed: 0.04,
        brakingAcceleration: 0.0006,
        creepSpeed: 0.0025,
        alignmentAngleDeg: 0.15,
        alignmentRate: 0.00001,
        alignmentLateral: 0.08,
        alignmentLateralSpeed: 0.002,
        pulseDeadband: 0.45,
        ...window.__pdConfig,
    };
    const state = {
        armed: false,
        frames: 0,
        finalApproachStartFrame: null,
        trace: [],
        telemetry: null,
        result: null,
        final: null,
        config,
    };
    window.__pdController = state;

    function clamp(value, limit) {
        return Math.max(-limit, Math.min(limit, value));
    }

    function approachAllowed(position, velocity, angles, rates, targets) {
        return Math.max(...angles.map(Math.abs)) * DEG <= config.alignmentAngleDeg &&
            Math.max(...rates.map(Math.abs)) <= config.alignmentRate &&
            targets.every(target => Math.abs(target) < rotationPulseSize / DEG / 2) &&
            Math.hypot(position.x, position.y) <= config.alignmentLateral &&
            Math.hypot(velocity.x, velocity.y) <= config.alignmentLateralSpeed;
    }

    function controlAttitude(rates, targets, actions) {
        // Quaternion rotation vector avoids treating coupled Euler errors as body axes.
        const q = camera.quaternion;
        const sign = q.w < 0 ? -1 : 1;
        const length = Math.hypot(q.x, q.y, q.z);
        const scale = length > 1e-12 ? 2 * Math.atan2(length, Math.abs(q.w)) / length : 2;
        const errors = [q.x, q.y, q.z].map(value => sign * scale * value);
        const controls = [
            [pitchDown, "pitchDown", pitchUp, "pitchUp"],
            [yawRight, "yawRight", yawLeft, "yawLeft"],
            [rollRight, "rollRight", rollLeft, "rollLeft"],
        ];
        errors.forEach((error, axis) => {
            // Positive rotation targets produce negative angular rates in this simulator.
            const desired = clamp(config.attitudeKp * error + config.attitudeKd * rates[axis],
                config.maxRotationTarget);
            pulse(desired - targets[axis], rotationPulseSize / DEG / 2, controls[axis], actions);
        });
    }

    function effort(error, velocity, speedLimit) {
        // u = Kp * (target - position) - Kd * velocity, with a speed-limited P term.
        return clamp(-config.positionKp * error, config.positionKd * speedLimit) -
            config.positionKd * velocity;
    }

    function pulse(error, deadband, controls, actions) {
        if (error > deadband) {
            controls[0]();
            actions.push(controls[1]);
        } else if (error < -deadband) {
            controls[2]();
            actions.push(controls[3]);
        }
    }

    function controlTranslation(position, velocity, allowed, actions) {
        // One continuous envelope slows the craft before the 0.2 m docking boundary.
        const brakingLimit = Math.sqrt(config.creepSpeed ** 2 +
            2 * config.brakingAcceleration * Math.max(0, position.z - 0.3));
        const lateralLimit = Math.min(config.lateralSpeed, brakingLimit);
        const approachLimit = allowed ? Math.min(config.approachSpeed, brakingLimit) : 0;
        const worldEffort = new THREE.Vector3(
            effort(position.x, velocity.x, lateralLimit),
            effort(position.y, velocity.y, lateralLimit),
            effort(position.z, velocity.z, approachLimit),
        );
        const inverse = camera.quaternion.clone();
        if (inverse.invert) inverse.invert();
        else inverse.inverse();
        const local = worldEffort.applyQuaternion(inverse);
        const deadband = config.positionKd * translationPulseSize * config.pulseDeadband;
        pulse(local.x, deadband, [translateRight, "translateRight", translateLeft, "translateLeft"], actions);
        pulse(local.y, deadband, [translateUp, "translateUp", translateDown, "translateDown"], actions);
        pulse(local.z, deadband, [translateBackward, "translateBackward", translateForward, "translateForward"], actions);
        return approachLimit;
    }

    function controlStep() {
        if (isGameOver || !state.armed) return;
        const actions = [];
        const position = camera.position.clone().sub(issObject.position);
        const velocity = motionVector.clone();
        // Gravity is a position displacement, not an acceleration of motionVector.
        if (isGravity) velocity.y -= gravity;
        const angles = [camera.rotation.x, camera.rotation.y, camera.rotation.z];
        const rates = [currentRotationX, currentRotationY, currentRotationZ];
        const targets = [targetRotationX, targetRotationY, targetRotationZ];
        const allowed = approachAllowed(position, velocity, angles, rates, targets);
        // This timestamp measures the deadline only; crossing 8 m changes no control law.
        if (position.z <= 8 && state.finalApproachStartFrame === null) {
            state.finalApproachStartFrame = state.frames;
        }
        controlAttitude(rates, targets, actions);
        const approachLimit = controlTranslation(position, velocity, allowed, actions);
        state.telemetry = {
            frame: state.frames,
            approach_allowed: allowed,
            approach_speed_limit_m_per_frame: approachLimit,
            position_m: { approach: position.z, lateral_y: position.x, lateral_z: position.y },
            velocity_m_per_frame: {
                approach: motionVector.z, lateral_y: motionVector.x, lateral_z: motionVector.y,
            },
            attitude_deg: { roll: angles[2] * DEG, pitch: angles[0] * DEG, yaw: angles[1] * DEG },
            range_m: position.length(),
            lateral_m: Math.hypot(position.x, position.y),
        };
        state.trace.push({ frame: state.frames, actions });
        state.frames += 1;
        window.__dockingFrame = state.frames;
    }

    const originalRender = render;
    window.render = function () {
        const wasGameOver = isGameOver;
        controlStep();
        const result = originalRender();
        if (state.armed && !wasGameOver && isGameOver) {
            const message = document.querySelector("#fail-message").textContent.trim();
            state.result = {
                outcome: message ? "FAIL" : "SUCCESS", message,
                frame: state.frames, telemetry: state.telemetry,
            };
            state.final = window.__readDockingState();
        }
        return result;
    };
})();
