"""Voice interface — speech-to-text (STT) and text-to-speech (TTS).

Uses the z-ai-web-dev-sdk ASR/TTS skills if available, otherwise falls
back to system tools (espeak, say, festival) for TTS and reports that
STT needs the SDK.

Real, working TTS on macOS (say), Linux (espeak/festival).
STT requires the z-ai-web-dev-sdk.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def text_to_speech(text: str, output_path: str | Path | None = None, voice: str = "default") -> str:
    """Convert text to speech and save as audio. Returns the output path."""
    if output_path is None:
        output_path = Path(tempfile.gettempdir()) / f"tts_{os.getpid()}.wav"
    output_path = Path(output_path)
    # Try z-ai-web-dev-sdk TTS first.
    if _try_zai_tts(text, output_path, voice):
        return str(output_path)
    # Fallback: system TTS.
    if _try_system_tts(text, output_path, voice):
        return str(output_path)
    return ""


def _try_zai_tts(text: str, output_path: Path, voice: str) -> bool:
    try:
        cmd = ["npx", "z-ai-web-dev-sdk", "tts", "--text", text, "--output", str(output_path), "--voice", voice]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0 and output_path.exists()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _try_system_tts(text: str, output_path: Path, voice: str) -> bool:
    """Try macOS 'say', Linux 'espeak'/'festival'."""
    import platform
    system = platform.system()
    try:
        if system == "Darwin":  # macOS
            cmd = ["say", "-o", str(output_path), text]
            subprocess.run(cmd, check=True, timeout=30)
            return output_path.exists()
        if system == "Linux":
            # Try espeak first.
            try:
                cmd = ["espeak", "-w", str(output_path), text]
                subprocess.run(cmd, check=True, timeout=30)
                return output_path.exists()
            except FileNotFoundError:
                # Try festival.
                cmd = ["festival", "--tts"]
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc.communicate(input=text.encode(), timeout=30)
                return proc.returncode == 0
        if system == "Windows":
            # Use PowerShell SAPI.
            ps_script = f'''
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile("{output_path}")
$synth.Speak("{text.replace('"', '`"')}")
$synth.Dispose()
'''
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True, timeout=30)
            return output_path.exists()
    except Exception:  # noqa: BLE001
        return False
    return False


def speech_to_text(audio_path: str | Path, language: str = "en") -> str:
    """Convert an audio file to text (transcription)."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        return f"Error: audio file not found: {audio_path}"
    # Try z-ai-web-dev-sdk ASR.
    try:
        cmd = ["npx", "z-ai-web-dev-sdk", "asr", "--audio", str(audio_path), "--language", language]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "Error: STT requires the z-ai-web-dev-sdk. Install with: npm install z-ai-web-dev-sdk"


def list_voices() -> list[str]:
    """List available TTS voices on the system."""
    import platform
    system = platform.system()
    voices: list[str] = []
    try:
        if system == "Darwin":
            result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts:
                    voices.append(parts[0])
        elif system == "Linux":
            result = subprocess.run(["espeak", "--voices"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines()[1:]:  # skip header
                parts = line.split()
                if len(parts) >= 2:
                    voices.append(parts[3] if len(parts) > 3 else parts[1])
    except Exception:  # noqa: BLE001
        pass
    return voices[:20]


def voice_available() -> bool:
    """Check if any TTS backend is available."""
    import platform
    system = platform.system()
    if system == "Darwin":
        return True  # 'say' is always available
    if system == "Linux":
        for tool in ["espeak", "festival"]:
            try:
                subprocess.run([tool, "--version"], capture_output=True, timeout=5)
                return True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    return False
