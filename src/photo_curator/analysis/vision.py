from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

LEGACY_VISION_TIMEOUT_SECONDS = 3.0
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VisionResult:
    face_count: int
    face_capture_quality: float | None
    eyes_detected: int


def vision_available() -> bool:
    try:
        import Vision  # noqa: F401
    except ImportError:
        return False
    return True


def analyze_faces(path: Path) -> VisionResult:
    """Run the retired PyObjC compatibility path without risking a process-wide hang."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "photo_curator.analysis.vision", "--worker", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=LEGACY_VISION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        LOGGER.warning("Legacy PyObjC Vision timed out for %s", path.name)
        return VisionResult(0, None, 0)
    if result.returncode != 0:
        LOGGER.warning("Legacy PyObjC Vision failed: %s", result.stderr[-500:])
        return VisionResult(0, None, 0)
    try:
        payload = json.loads(result.stdout)
        return VisionResult(
            face_count=int(payload["face_count"]),
            face_capture_quality=(
                float(payload["face_capture_quality"])
                if payload.get("face_capture_quality") is not None
                else None
            ),
            eyes_detected=int(payload["eyes_detected"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        LOGGER.warning("Legacy PyObjC Vision returned an invalid result")
        return VisionResult(0, None, 0)


def _analyze_faces_in_process(path: Path) -> VisionResult:
    try:
        import Vision
        from Foundation import NSURL
    except ImportError:
        return VisionResult(0, None, 0)

    url = NSURL.fileURLWithPath_(str(path))
    landmarks_request = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    quality_request = Vision.VNDetectFaceCaptureQualityRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    success, error = handler.performRequests_error_([landmarks_request, quality_request], None)
    if not success:
        raise OSError(str(error or "Apple Vision request failed"))
    landmarks = list(landmarks_request.results() or [])
    qualities = [
        float(value)
        for observation in list(quality_request.results() or [])
        if (value := observation.faceCaptureQuality()) is not None
    ]
    eyes = 0
    for observation in landmarks:
        face_landmarks = observation.landmarks()
        if face_landmarks and face_landmarks.leftEye() and face_landmarks.rightEye():
            eyes += 1
    return VisionResult(
        face_count=len(landmarks),
        face_capture_quality=max(qualities) if qualities else None,
        eyes_detected=eyes,
    )


def _main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        return 2
    try:
        result = _analyze_faces_in_process(Path(sys.argv[2]))
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
