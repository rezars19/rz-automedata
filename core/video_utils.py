"""
RZ Automedata - Video Utilities
Extract frames from video files for AI analysis.
"""

import cv2
import os
from PIL import Image
import numpy as np


def extract_frames(video_path, num_frames=5):
    """
    Extract evenly spaced frames from a video file.
    
    Args:
        video_path: Path to the video file (MP4, MOV)
        num_frames: Number of frames to extract (default: 5)
    
    Returns:
        List of PIL.Image objects
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video file: {video_path}")
    
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        raise ValueError(f"Video has no frames: {video_path}")
    
    # Calculate evenly spaced frame indices
    if total_frames < num_frames:
        frame_indices = list(range(total_frames))
    else:
        frame_indices = [int(i * (total_frames - 1) / (num_frames - 1)) for i in range(num_frames)]
    
    frames = []
    for idx in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            # Convert BGR to RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(frame_rgb)
            frames.append(pil_image)
    
    cap.release()
    return frames


def get_video_thumbnail(video_path, size=(200, 150)):
    """
    Get a single thumbnail from the middle of the video for preview.
    
    Args:
        video_path: Path to the video file
        size: Thumbnail size tuple (width, height)
    
    Returns:
        PIL.Image object (thumbnail)
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video file: {video_path}")
    
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    middle_frame = total_frames // 2
    
    cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame)
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        raise ValueError(f"Cannot read frame from video: {video_path}")
    
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(frame_rgb)
    pil_image.thumbnail(size, Image.LANCZOS)
    return pil_image
