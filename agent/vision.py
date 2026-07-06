"""Vision/multimodal support — analyze images, screenshots, diagrams.

Uses the z-ai-web-dev-sdk VLM skill if available, otherwise falls back to
a heuristic image analyzer (file metadata + basic properties).

Supports:
  * Image URL analysis
  * Base64-encoded image analysis
  * Screenshot description
  * UI bug detection (basic)
  * Diagram/chart interpretation
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


@dataclass
class ImageAnalysis:
    description: str
    width: int = 0
    height: int = 0
    format: str = ""
    size_bytes: int = 0
    objects: list[str] = None
    text_extracted: str = ""
    colors: list[str] = None


def analyze_image(path: str | Path | None = None, url: str | None = None, prompt: str = "Describe this image in detail.") -> ImageAnalysis:
    """Analyze an image from a file path or URL.

    Uses the VLM SDK if available; otherwise returns basic file metadata.
    """
    if path is not None:
        p = Path(path)
        if not p.exists():
            return ImageAnalysis(description=f"Error: file not found: {path}")
        size = p.stat().st_size
        ext = p.suffix.lower().lstrip(".")
        # Try to read basic image dimensions if PIL is available.
        width, height = 0, 0
        try:
            from PIL import Image
            with Image.open(p) as img:
                width, height = img.size
        except ImportError:
            pass
        except Exception:  # noqa: BLE001
            pass
        # Try VLM SDK.
        description = _vlm_analyze_file(p, prompt)
        if not description:
            description = f"Image file ({ext}, {size} bytes, {width}x{height}). VLM not available — install z-ai-web-dev-sdk for full analysis."
        return ImageAnalysis(
            description=description,
            width=width,
            height=height,
            format=ext,
            size_bytes=size,
        )
    if url is not None:
        description = _vlm_analyze_url(url, prompt)
        if not description:
            description = f"Image at {url}. VLM not available for full analysis."
        return ImageAnalysis(description=description)
    return ImageAnalysis(description="Error: provide either path or url")


def _vlm_analyze_file(path: Path, prompt: str) -> str:
    """Use the VLM skill to analyze a local image file."""
    try:
        # The z-ai-web-dev-sdk VLM expects base64.
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode()
        return _vlm_call(prompt, image_base64=b64)
    except Exception:  # noqa: BLE001
        return ""


def _vlm_analyze_url(url: str, prompt: str) -> str:
    """Use the VLM skill to analyze an image at a URL."""
    return _vlm_call(prompt, image_url=url)


def _vlm_call(prompt: str, image_url: str | None = None, image_base64: str | None = None) -> str:
    """Call the VLM via the z-ai-web-dev-sdk CLI if available."""
    import subprocess
    try:
        cmd = ["npx", "z-ai-web-dev-sdk", "vlm", "--prompt", prompt]
        if image_url:
            cmd += ["--image-url", image_url]
        if image_base64:
            cmd += ["--image-base64", image_base64]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def detect_ui_bugs(screenshot_path: str | Path) -> list[str]:
    """Heuristic UI bug detection from a screenshot (basic)."""
    bugs: list[str] = []
    analysis = analyze_image(path=screenshot_path, prompt="Identify any UI bugs, layout issues, overlapping elements, or visual problems in this screenshot.")
    if analysis.description and "bug" in analysis.description.lower():
        bugs.append(analysis.description)
    return bugs


def extract_text_from_image(path: str | Path) -> str:
    """OCR-like text extraction from an image (uses VLM if available)."""
    analysis = analyze_image(path=path, prompt="Extract all visible text from this image. Output only the text, nothing else.")
    return analysis.description or ""


def describe_colors(path: str | Path) -> list[str]:
    """Extract the dominant colors from an image."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            img = img.convert("RGB").resize((100, 100))
            pixels = list(img.getdata())
            # Simple quantisation.
            color_counts: dict[tuple, int] = {}
            for r, g, b in pixels:
                key = (r // 32 * 32, g // 32 * 32, b // 32 * 32)
                color_counts[key] = color_counts.get(key, 0) + 1
            sorted_colors = sorted(color_counts.items(), key=lambda x: -x[1])[:5]
            return [f"#{r:02x}{g:02x}{b:02x}" for (r, g, b), _ in sorted_colors]
    except ImportError:
        return []
    except Exception:  # noqa: BLE001
        return []
