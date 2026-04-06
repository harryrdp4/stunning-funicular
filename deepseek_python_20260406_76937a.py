#!/usr/bin/env python3
import gradio as gr
import subprocess
import threading
import time
import signal
import os
import re

# Global variable to hold the current ffmpeg process
current_process = None
log_lines = []
log_lock = threading.Lock()

def log(message):
    """Add a timestamped message to the log."""
    with log_lock:
        log_lines.append(f"[{time.strftime('%H:%M:%S')}] {message}")
    return "\n".join(log_lines[-50:])  # keep last 50 lines

def build_rtmp_url(stream_key):
    """Build YouTube RTMP URL from a stream key."""
    if stream_key.startswith("rtmp://"):
        return stream_key  # user provided full URL
    return f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"

def start_stream(video_path, stream_key, width, height, framerate, bitrate, crf, preset, tune, audio_bitrate):
    """Start the ffmpeg streaming process."""
    global current_process

    if current_process is not None and current_process.poll() is None:
        return log("Stream already running. Stop it first.")

    if not video_path or not os.path.exists(video_path):
        return log(f"Video file not found: {video_path}")

    if not stream_key:
        return log("Please provide a YouTube stream key or full RTMP URL.")

    rtmp_url = build_rtmp_url(stream_key)
    log(f"Starting stream to: {rtmp_url}")

    # Build ffmpeg command
    cmd = [
        "ffmpeg",
        "-stream_loop", "-1",   # loop forever
        "-re",                  # read at native frame rate
        "-i", video_path,
        "-r", str(framerate),
        "-s", f"{width}x{height}",
        "-vcodec", "libx264",
        "-preset", preset,
        "-tune", tune,
        "-crf", str(crf),
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",
        "-b:v", f"{bitrate}k",
        "-maxrate", f"{bitrate*1.1:.0f}k",
        "-bufsize", f"{bitrate*2.2:.0f}k",
        "-acodec", "aac",
        "-b:a", f"{audio_bitrate}k",
        "-ar", "44100",
        "-f", "flv",
        rtmp_url
    ]

    # Print command for debugging (optional)
    log("Command: " + " ".join(cmd))

    try:
        # Start the process, redirect stderr to capture logs
        current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        # Start a thread to read stderr (ffmpeg logs go to stderr)
        def read_stderr():
            if current_process is None:
                return
            for line in iter(current_process.stderr.readline, ""):
                if line:
                    log(f"ffmpeg: {line.strip()}")
                if current_process.poll() is not None:
                    break
            # After process ends, show exit code
            rc = current_process.poll()
            log(f"ffmpeg exited with code {rc}")

        threading.Thread(target=read_stderr, daemon=True).start()
        return log("Stream started (looping video).")
    except Exception as e:
        return log(f"Failed to start ffmpeg: {e}")

def stop_stream():
    """Stop the currently running ffmpeg stream."""
    global current_process
    if current_process is None or current_process.poll() is not None:
        return log("No running stream to stop.")

    log("Stopping stream...")
    current_process.terminate()
    # Wait a bit, then force kill if needed
    try:
        current_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        current_process.kill()
        log("Force killed ffmpeg.")
    current_process = None
    return log("Stream stopped.")

def clear_log():
    """Clear the log display."""
    global log_lines
    with log_lock:
        log_lines.clear()
    return ""

# Create Gradio UI
with gr.Blocks(title="YouTube Live Streamer", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🎥 YouTube Live Stream from a Video File (Looping)")
    gr.Markdown("Stream any MP4 file to YouTube Live using ffmpeg. The video will loop continuously.")

    with gr.Row():
        with gr.Column(scale=2):
            video_file = gr.Textbox(label="Video File Path", placeholder="/path/to/video.mp4",
                                    value="/content/1.mp4")
            gr.Markdown("Or use the file uploader below:")
            video_upload = gr.File(label="Upload Video File", file_types=[".mp4", ".mkv", ".mov"])
            stream_key = gr.Textbox(label="YouTube Stream Key (or full RTMP URL)",
                                    placeholder="t62u-k4gg-sat8-bzyb-5ecv",
                                    value="t62u-k4gg-sat8-bzyb-5ecv")
        with gr.Column(scale=1):
            width = gr.Number(label="Width", value=1920, step=2)
            height = gr.Number(label="Height", value=1080, step=2)
            framerate = gr.Number(label="Framerate", value=30, step=1)
            bitrate = gr.Number(label="Video Bitrate (kbps)", value=6500, step=100)
            crf = gr.Slider(label="CRF (quality, lower = better)", minimum=0, maximum=51, value=21, step=1)
            preset = gr.Dropdown(label="Preset", choices=["ultrafast", "superfast", "veryfast", "faster", "fast"],
                                 value="ultrafast")
            tune = gr.Dropdown(label="Tune", choices=["zerolatency", "film", "animation", "stillimage"],
                               value="zerolatency")
            audio_bitrate = gr.Number(label="Audio Bitrate (kbps)", value=160, step=16)

    with gr.Row():
        start_btn = gr.Button("▶️ Start Streaming", variant="primary")
        stop_btn = gr.Button("⏹️ Stop Streaming", variant="stop")
        clear_log_btn = gr.Button("🗑️ Clear Log")

    log_output = gr.Textbox(label="Stream Log", lines=20, interactive=False, autoscroll=True)

    # When a file is uploaded, update the video_file textbox
    def set_video_path(file_obj):
        if file_obj:
            return file_obj.name
        return ""
    video_upload.change(fn=set_video_path, inputs=video_upload, outputs=video_file)

    # Button actions
    start_btn.click(
        fn=start_stream,
        inputs=[video_file, stream_key, width, height, framerate, bitrate, crf, preset, tune, audio_bitrate],
        outputs=log_output
    )
    stop_btn.click(fn=stop_stream, inputs=[], outputs=log_output)
    clear_log_btn.click(fn=clear_log, inputs=[], outputs=log_output)

    # Periodically refresh the log (every 0.5s) to show new lines
    demo.load(fn=lambda: gr.update(every=0.5), inputs=None, outputs=None)  # just triggers refresh

    # A trick to auto-refresh the log: we use a timer in js, but simpler:
    # We can use gradio's built-in .then() to update log after each action.
    # However, for continuous updates while ffmpeg runs, we need polling.
    # We'll add a javascript interval via custom component? Not necessary.
    # Instead, we rely on the user pressing start/stop and we return full log each time.
    # But to see real-time progress, we can stream logs using gr.Textbox's .change?
    # Let's use a simple loop in python that updates the log every second? Not gradio-friendly.
    # Alternative: use gradio's `every` parameter on a component? Not straightforward.
    # A robust way: run a background thread that updates a state and use gr.Textbox(autoscroll=True)
    # but the UI will only update when an event triggers. So we add a "Refresh Log" button.
    # For better UX, we'll add a refresh button and also auto-refresh every 2 seconds via JS.

    # Add a manual refresh button
    refresh_log_btn = gr.Button("🔄 Refresh Log")
    def refresh_log():
        with log_lock:
            return "\n".join(log_lines[-50:])
    refresh_log_btn.click(fn=refresh_log, inputs=[], outputs=log_output)

    # Auto-refresh using JS (poll every 2s)
    gr.HTML("""
    <script>
    function refreshLog() {
        const refreshBtn = document.querySelector('button:has(> :text("🔄 Refresh Log"))');
        if (refreshBtn) refreshBtn.click();
    }
    setInterval(refreshLog, 2000);
    </script>
    """)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)