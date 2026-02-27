"""
RZ Automedata — Upscaler Client (Google Drive Polling)
Communicates with Google Colab through Google Drive JSON files.
No ngrok, no tunnels — zero network dependency.
"""

import logging

logger = logging.getLogger(__name__)

# Model definitions
MODELS = {
    "realesr-animevideov3": {
        "name": "🚀 AnimVideo v3 (Fastest)",
        "desc": "Optimized for video, 3-5x faster",
        "scales": [2, 3, 4],
    },
    "RealESRGAN_x4plus": {
        "name": "💎 x4plus (Best Quality)",
        "desc": "Highest quality, slower",
        "scales": [4],
    },
}

DEFAULT_MODEL = "realesr-animevideov3"


class UpscalerClient:
    """Google Drive-based client for the Colab upscaler."""

    def __init__(self):
        self._gdrive_bridge = None
        self._last_known = {}

    def set_bridge(self, bridge):
        self._gdrive_bridge = bridge

    @property
    def is_ready(self):
        return self._gdrive_bridge is not None and self._gdrive_bridge.is_configured

    def start_process(self, task_id, filename, scale=4,
                      model="realesr-animevideov3",
                      face_enhance=False, mute_audio=False,
                      output_format="mp4", target_fps=30):
        if not self.is_ready:
            raise RuntimeError("Google Drive not configured.")
        self._gdrive_bridge.write_job(
            task_id=task_id, filename=filename, scale=scale,
            model=model, face_enhance=face_enhance,
            mute_audio=mute_audio, output_format=output_format,
            target_fps=target_fps,
        )
        return True

    def poll_status(self, task_id):
        if not self.is_ready:
            return {
                "status": "error", "progress": 0,
                "stage": "Drive not configured", "error": "Google Drive not set",
                "log": [],
            }
        data = self._gdrive_bridge.read_status(task_id)
        if data is not None:
            result = {
                "status": data.get("status", "unknown"),
                "progress": data.get("progress", 0),
                "stage": data.get("stage", ""),
                "error": data.get("error"),
                "log": data.get("log", []),
            }
            self._last_known[task_id] = result
            return result
        if task_id in self._last_known:
            return self._last_known[task_id]
        return {
            "status": "waiting", "progress": 0,
            "stage": "Waiting for Colab...", "error": None, "log": [],
        }
