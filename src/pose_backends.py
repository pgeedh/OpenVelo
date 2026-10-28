"""
Pluggable pose-estimation backends.

All backends return the same structure per frame:
    {name: (x_px, y_px, confidence)}  using OpenVelo key names
    (left_/right_ + shoulder, elbow, wrist, hip, knee, ankle, heel, toe, ear, eye; nose)

Backends
--------
mediapipe  : Google MediaPipe Pose Landmarker (Heavy). Bundled, works offline. Default.
rtmpose    : OpenMMLab RTMPose-m, Halpe-26 (body + heels + toes) via rtmlib/ONNX Runtime.
             Higher accuracy on cyclists, model auto-downloads (~100 MB) on first use.
rtmpose-x  : RTMPose-x 384x288 + YOLOX-x detector. Best accuracy, slowest.
"""
import os

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MP_MODEL = os.path.join(ROOT, 'src', 'models', 'pose_landmarker_heavy.task')

BACKENDS = {
    'mediapipe': {
        'name': 'MediaPipe Pose Heavy',
        'desc': 'Google BlazePose GHUM, 33 keypoints incl. heel/toe. Bundled, so it works offline.',
        'speed': 'Fast',
    },
    'rtmpose': {
        'name': 'RTMPose-m · Halpe-26',
        'desc': 'OpenMMLab real-time SOTA, 26 keypoints incl. heels & toes. Downloads model on first run.',
        'speed': 'Balanced',
    },
    'rtmpose-x': {
        'name': 'RTMPose-x · Halpe-26',
        'desc': 'Largest RTMPose (384×288) + YOLOX-x detector. Highest accuracy, slowest.',
        'speed': 'Accurate',
    },
}

MP_MAP = {
    0: 'nose', 2: 'left_eye', 5: 'right_eye', 7: 'left_ear', 8: 'right_ear',
    11: 'left_shoulder', 12: 'right_shoulder', 13: 'left_elbow', 14: 'right_elbow',
    15: 'left_wrist', 16: 'right_wrist', 23: 'left_hip', 24: 'right_hip',
    25: 'left_knee', 26: 'right_knee', 27: 'left_ankle', 28: 'right_ankle',
    29: 'left_heel', 30: 'right_heel', 31: 'left_toe', 32: 'right_toe',
}

HALPE26_MAP = {
    0: 'nose', 1: 'left_eye', 2: 'right_eye', 3: 'left_ear', 4: 'right_ear',
    5: 'left_shoulder', 6: 'right_shoulder', 7: 'left_elbow', 8: 'right_elbow',
    9: 'left_wrist', 10: 'right_wrist', 11: 'left_hip', 12: 'right_hip',
    13: 'left_knee', 14: 'right_knee', 15: 'left_ankle', 16: 'right_ankle',
    20: 'left_toe', 21: 'right_toe', 24: 'left_heel', 25: 'right_heel',
}


def available():
    """Which backends can be imported in this environment."""
    out = {'mediapipe': os.path.exists(MP_MODEL)}
    try:
        import rtmlib  # noqa: F401
        import onnxruntime  # noqa: F401
        out['rtmpose'] = out['rtmpose-x'] = True
    except Exception:
        out['rtmpose'] = out['rtmpose-x'] = False
    return out


class MediaPipeBackend:
    def __init__(self, mode='video'):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        if not os.path.exists(MP_MODEL):
            raise FileNotFoundError("MediaPipe model missing: src/models/pose_landmarker_heavy.task")
        self.mp = mp
        self.mode = mode
        opts = vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=MP_MODEL),
            running_mode=vision.RunningMode.IMAGE if mode == 'image' else vision.RunningMode.VIDEO,
            num_poses=1, min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5, min_tracking_confidence=0.5,
            output_segmentation_masks=True)
        self.last_mask = None
        self.landmarker = vision.PoseLandmarker.create_from_options(opts)

    def process(self, frame_bgr, ts_ms=0):
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        res = self.landmarker.detect(img) if self.mode == 'image' else \
            self.landmarker.detect_for_video(img, int(ts_ms))
        # Person silhouette (0-1 float, frame size); posture.py reads the back from it
        self.last_mask = res.segmentation_masks[0].numpy_view().squeeze() if res.segmentation_masks else None
        if not res.pose_landmarks:
            return {}
        h, w = frame_bgr.shape[:2]
        lms = res.pose_landmarks[0]
        # 4th value: relative depth (smaller = closer to camera), used to tell
        # the near leg from the far leg.
        return {name: (lms[i].x * w, lms[i].y * h, float(lms[i].visibility or 0.0), float(lms[i].z))
                for i, name in MP_MAP.items() if i < len(lms)}

    def close(self):
        try:
            self.landmarker.close()
        except Exception:
            pass


class RTMPoseBackend:
    def __init__(self, mode='video', size='m'):
        from rtmlib import BodyWithFeet, PoseTracker
        from functools import partial
        model_mode = 'performance' if size == 'x' else 'balanced'
        self.last_mask = None  # no segmentation: back roundness is unavailable
        solution = partial(BodyWithFeet, mode=model_mode)
        if mode == 'image':
            self.model = BodyWithFeet(mode=model_mode, backend='onnxruntime', device='cpu')
        else:
            # Detector runs every N frames; RTMPose tracks the box in between.
            self.model = PoseTracker(solution, det_frequency=8, tracking=False,
                                     backend='onnxruntime', device='cpu')

    def process(self, frame_bgr, ts_ms=0):
        kps, scores = self.model(frame_bgr)
        if kps is None or len(kps) == 0:
            return {}
        kps, scores = np.asarray(kps), np.asarray(scores)
        if kps.ndim == 2:
            kps, scores = kps[None], scores[None]
        # Pick the largest person (the rider is normally closest to camera)
        areas = [np.ptp(k[:, 0]) * np.ptp(k[:, 1]) for k in kps]
        i = int(np.argmax(areas))
        k, s = kps[i], scores[i]
        # RTMPose SimCC scores are not probabilities; squash to 0-1.
        conf = np.clip(s / 1.0, 0, 1) if s.max() <= 1.5 else np.clip(s / 8.0, 0, 1)
        return {name: (float(k[j, 0]), float(k[j, 1]), float(conf[j]))
                for j, name in HALPE26_MAP.items() if j < len(k)}

    def close(self):
        pass


def make_backend(name='mediapipe', mode='video'):
    if name == 'mediapipe':
        return MediaPipeBackend(mode)
    if name == 'rtmpose':
        return RTMPoseBackend(mode, 'm')
    if name == 'rtmpose-x':
        return RTMPoseBackend(mode, 'x')
    raise ValueError(f"Unknown backend '{name}'. Choose from {list(BACKENDS)}")
