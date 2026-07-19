from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    """Run Apple's on-device Vision framework; no image data leaves the Mac."""
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
