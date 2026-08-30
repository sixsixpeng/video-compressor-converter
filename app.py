"""本地视频压缩与格式转换工具（NiceGUI + PyAV）。"""
from __future__ import annotations

import atexit
import asyncio
import json
import os
import shutil
import tempfile
import uuid
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Optional

import av
from nicegui import Client, app, background_tasks, run, ui

WORK_DIR = Path(tempfile.mkdtemp(prefix="video_converter_"))
atexit.register(lambda: shutil.rmtree(WORK_DIR, ignore_errors=True))


async def shutdown_after_last_client_disconnect(_) -> None:
    """关闭最后一个浏览器页面后，延迟确认无客户端连接再退出本地服务。"""
    await asyncio.sleep(2)
    connected_clients = [client for client in Client.instances.values() if not client.is_auto_index_client and client.has_socket_connection]
    if not connected_clients:
        app.shutdown()


app.on_disconnect(shutdown_after_last_client_disconnect)

FORMAT_SETTINGS = {
    "MP4 (H.264 + AAC，通用兼容)": {"extension": "mp4", "container": "mp4", "video": "libx264", "audio": "aac"},
    "MP4 (H.265 / HEVC + AAC，更小体积)": {"extension": "mp4", "container": "mp4", "video": "libx265", "audio": "aac"},
    "M4V (H.264 + AAC，Apple 设备)": {"extension": "m4v", "container": "mp4", "video": "libx264", "audio": "aac"},
    "MOV (H.264 + AAC，剪辑软件)": {"extension": "mov", "container": "mov", "video": "libx264", "audio": "aac"},
    "WebM (VP9 + Opus)": {"extension": "webm", "container": "webm", "video": "libvpx-vp9", "audio": "libopus"},
    "MKV (H.264 + AAC)": {"extension": "mkv", "container": "matroska", "video": "libx264", "audio": "aac"},
    "MKV (H.265 / HEVC + AAC)": {"extension": "mkv", "container": "matroska", "video": "libx265", "audio": "aac"},
    "MKV (VP9 + Opus)": {"extension": "mkv", "container": "matroska", "video": "libvpx-vp9", "audio": "libopus"},
    "MPEG-TS (H.264 + AAC，电视 / 流媒体)": {"extension": "ts", "container": "mpegts", "video": "libx264", "audio": "aac"},
    "FLV (H.264 + AAC，旧式直播兼容)": {"extension": "flv", "container": "flv", "video": "libx264", "audio": "aac"},
    "AVI (MPEG-4 + MP3)": {"extension": "avi", "container": "avi", "video": "mpeg4", "audio": "mp3"},
}
VIDEO_EXTENSIONS = {".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv"}
CRF_CHOICES = [18, 20, 23, 26, 28, 30, 33, 35, 40]
WIDTH_CHOICES = [426, 640, 854, 960, 1280, 1920, 2560, 3840]
FPS_CHOICES = [24, 25, 30, 50, 60, 120]
BITRATE_CHOICES = [500, 800, 1000, 1500, 2500, 4000, 6000, 8000, 12000, 20000]
CUSTOM_OUTPUT_MODE = "自定义输出目录"
SOURCE_OUTPUT_MODE = "原文件夹输出"
OUTPUT_MODE_OPTIONS = {CUSTOM_OUTPUT_MODE: CUSTOM_OUTPUT_MODE, SOURCE_OUTPUT_MODE: SOURCE_OUTPUT_MODE}
LEGACY_OUTPUT_MODE_MAP = {"统一目录": CUSTOM_OUTPUT_MODE, "源文件所在目录": SOURCE_OUTPUT_MODE}


def normalise_even(value: int) -> int:
    """返回适合 yuv420p 的、至少为 2 的偶数尺寸。"""
    return max(2, value - value % 2)


def parse_optional_positive_int(value: object, label: str) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = int(str(value))
    except ValueError as exc:
        raise ValueError(f"{label}必须是正整数。") from exc
    if result <= 0:
        raise ValueError(f"{label}必须大于 0。")
    return result


def parse_crf(value: object) -> int:
    try:
        crf = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("CRF 必须是 0 到 51 的整数。") from exc
    if not 0 <= crf <= 51:
        raise ValueError("CRF 必须在 0 到 51 之间。")
    return crf


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total_seconds = round(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def format_bitrate(bits_per_second: Optional[int]) -> str:
    if not bits_per_second or bits_per_second <= 0:
        return "—"
    if bits_per_second >= 1_000_000:
        return f"{bits_per_second / 1_000_000:.2f} Mbps"
    return f"{bits_per_second / 1_000:.0f} kbps"


def probe_video(path: Path) -> dict[str, object]:
    """读取首个视频流的展示元数据，不解码完整视频。"""
    with av.open(path) as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("未找到视频流。")
        context = stream.codec_context
        fps = stream.average_rate
        source_fps = round(float(fps)) if fps else None
        duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else None
        if duration is None and container.duration is not None:
            duration = float(container.duration / av.time_base)
        bitrate = container.bit_rate or getattr(context, "bit_rate", None)
        source_bitrate_kbps = round(bitrate / 1_000) if bitrate else None
        return {
            "codec": str(context.name or "未知").upper(),
            "dimensions": f"{context.width} × {context.height}",
            "fps": f"{float(fps):.3g}" if fps else "—",
            "duration": format_duration(duration),
            "bitrate": format_bitrate(bitrate),
            "source_width": context.width,
            "source_height": context.height,
            "source_fps": source_fps,
            "source_bitrate_kbps": source_bitrate_kbps,
        }


def build_output_name(input_name: str, extension: str) -> str:
    return f"{Path(input_name).stem}_converted.{extension}"


def normalise_output_mode(value: object) -> str:
    """兼容旧 UI 文案，并统一转换时使用的输出位置模式。"""
    if value is None or str(value).strip() == "":
        return CUSTOM_OUTPUT_MODE
    raw_value = str(value)
    return LEGACY_OUTPUT_MODE_MAP.get(raw_value, raw_value)


def choose_effective_output_directory(output_mode: object, custom_output_dir: Path,
                                      source_directory: object) -> tuple[Path, str, bool]:
    """根据输出模式返回实际输出目录、最终模式、是否因缺少源目录而回退。"""
    normalised_mode = normalise_output_mode(output_mode)
    if normalised_mode == SOURCE_OUTPUT_MODE:
        if source_directory is not None:
            return resolve_output_directory(source_directory), SOURCE_OUTPUT_MODE, False
        return custom_output_dir, CUSTOM_OUTPUT_MODE, True
    return custom_output_dir, CUSTOM_OUTPUT_MODE, False


def resolve_output_directory(value: object) -> Path:
    """获取输出目录；留空时使用当前用户的 Downloads 目录。"""
    raw_path = str(value or "").strip()
    output_dir = Path(raw_path).expanduser() if raw_path else Path.home() / "Downloads"
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise ValueError(f"输出目录无效：{output_dir}")
    return output_dir.resolve()


def choose_output_directory(initial_directory: Path) -> Optional[Path]:
    """打开 Windows 原生文件夹选择窗口；用户取消时返回 None。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title="选择视频输出目录", initialdir=str(initial_directory))
    finally:
        root.destroy()
    return Path(selected).resolve() if selected else None


def choose_input_files(initial_directory: Path) -> list[Path]:
    """通过 Windows 原生窗口选择一个或多个视频文件。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilenames(
            title="选择视频文件",
            initialdir=str(initial_directory),
            filetypes=[("视频文件", "*.mp4 *.mkv *.mov *.avi *.webm *.wmv *.m4v *.mpeg *.mpg *.ts *.mts *.m2ts *.flv"), ("所有文件", "*.*")],
        )
    finally:
        root.destroy()
    return [Path(path).resolve() for path in selected]


def scan_video_directory(directory: Path) -> list[Path]:
    """递归查找文件夹内支持的视频文件，返回稳定排序的绝对路径。"""
    return sorted(
        (path.resolve() for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS),
        key=lambda path: str(path).lower(),
    )


def choose_input_directory(initial_directory: Path) -> list[Path]:
    """选择目录后递归扫描其中的视频文件。"""
    directory = choose_output_directory(initial_directory)
    return scan_video_directory(directory) if directory else []


def build_available_output_path(output_dir: Path, input_name: str, extension: str) -> Path:
    """避免覆盖已有文件，为重复导出自动追加序号。"""
    desired_name = build_output_name(input_name, extension)
    candidate = output_dir / desired_name
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"{Path(desired_name).stem}_{suffix}.{extension}"
        suffix += 1
    return candidate


def target_size(source_width: int, source_height: int, max_width: Optional[int]) -> tuple[int, int]:
    if not max_width or max_width >= source_width:
        return normalise_even(source_width), normalise_even(source_height)
    height = round(source_height * max_width / source_width)
    return normalise_even(max_width), normalise_even(height)


def configure_video_stream(container: av.container.OutputContainer, settings: dict, width: int, height: int,
                           rate: object, crf: int, preset: str, target_bitrate_kbps: Optional[int]) -> av.video.stream.VideoStream:
    stream = container.add_stream(settings["video"], rate=rate)
    stream.width, stream.height = width, height
    stream.pix_fmt = "yuv420p"
    if target_bitrate_kbps:
        stream.bit_rate = target_bitrate_kbps * 1_000
        stream.options = {"preset": preset} if settings["video"] in {"libx264", "libx265"} else {}
    elif settings["video"] in {"libx264", "libx265"}:
        stream.options = {"crf": str(crf), "preset": preset}
    elif settings["video"] == "libvpx-vp9":
        stream.options = {"crf": str(crf), "b:v": "0", "deadline": "good" if preset != "ultrafast" else "realtime"}
    else:
        stream.options = {"qscale": str(max(1, min(31, round(crf * 31 / 51))))}
    return stream


def convert_video(input_path: str, output_path: str, output_label: str, crf: int,
                  preset: str, max_width: Optional[int], output_fps: Optional[int],
                  target_bitrate_kbps: Optional[int] = None) -> None:
    """同步执行转码，供 NiceGUI 的 io_bound 线程调用。"""
    settings = FORMAT_SETTINGS[output_label]
    with av.open(input_path) as source:
        video_input = next((stream for stream in source.streams if stream.type == "video"), None)
        if video_input is None:
            raise ValueError("未在此文件中找到视频流。")

        width, height = target_size(video_input.codec_context.width, video_input.codec_context.height, max_width)
        rate = output_fps or video_input.average_rate or 30
        output_time_base = Fraction(1, 1) / Fraction(rate)
        previous_video_pts = -1
        audio_input = next((stream for stream in source.streams if stream.type == "audio"), None)

        with av.open(output_path, mode="w", format=settings["container"]) as target:
            video_output = configure_video_stream(target, settings, width, height, rate, crf, preset, target_bitrate_kbps)
            audio_output = None
            audio_resampler = None
            if audio_input is not None:
                sample_rate = audio_input.codec_context.sample_rate or 48_000
                audio_output = target.add_stream(settings["audio"], rate=sample_rate)
                audio_resampler = av.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=sample_rate)

            for packet in source.demux((video_input, audio_input) if audio_input else video_input):
                if packet.stream.type == "video":
                    for frame in packet.decode():
                        timestamp = frame.time
                        frame = frame.reformat(width=width, height=height, format="yuv420p")
                        if timestamp is not None:
                            output_pts = round(float(timestamp) / float(output_time_base))
                            if output_pts <= previous_video_pts:
                                continue
                            previous_video_pts = output_pts
                            frame.pts = output_pts
                            frame.time_base = output_time_base
                        for encoded in video_output.encode(frame):
                            target.mux(encoded)
                elif audio_output is not None and packet.stream.type == "audio":
                    for frame in packet.decode():
                        for resampled in audio_resampler.resample(frame):
                            for encoded in audio_output.encode(resampled):
                                target.mux(encoded)

            for encoded in video_output.encode():
                target.mux(encoded)
            if audio_output is not None:
                for resampled in audio_resampler.resample(None):
                    for encoded in audio_output.encode(resampled):
                        target.mux(encoded)
                for encoded in audio_output.encode():
                    target.mux(encoded)


ui.add_head_html('''
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;500;700&display=swap');
:root { --ink:#1d1d1d; --paper:#ffffff; --card:#ffffff; --accent:var(--q-primary); --warning:#c10015; --line:#e0e0e0; --guide-a:#ffffff; --guide-b:#f5f5f5; --shadow:rgba(0,0,0,.12); }
body.body--dark { --ink:#f5f5f5; --paper:#121212; --card:#1d1d1d; --accent:var(--q-primary); --warning:#ff8a80; --line:#3a3a3a; --guide-a:#1d1d1d; --guide-b:#242424; --shadow:rgba(0,0,0,.45); }
body { background:var(--paper); color:var(--ink); font-family:'Noto Sans SC',sans-serif; transition:background .28s ease,color .28s ease; }
.hero-grid { background:none; }
.tool-card { border:1px solid var(--line); box-shadow:0 2px 10px var(--shadow); background:var(--card); transition:background .28s ease,border-color .28s ease,box-shadow .28s ease; }
.format-guide { border:1px solid var(--line); background:linear-gradient(135deg,var(--guide-a) 0%,var(--guide-b) 100%); box-shadow:0 1px 4px var(--shadow); }
.format-guide strong { color:var(--warning); }
.mono { font-family:'DM Mono', monospace; letter-spacing:.08em; }
.official-theme-switch { background:var(--card); border:1px solid var(--line); padding:.45rem .75rem; color:var(--ink); }
.q-table__container, .q-table__card, .q-menu, .q-field__control { background:var(--card) !important; color:var(--ink) !important; }
.q-table th, .q-table td { color:var(--ink) !important; border-color:var(--line) !important; }
.q-table thead tr { background:color-mix(in srgb,var(--accent) 10%,var(--card)); }
.q-btn { letter-spacing:.025em; }
.page-drop-overlay { position:fixed; inset:0; z-index:9999; display:none; align-items:center; justify-content:center; padding:2rem; background:color-mix(in srgb,var(--paper) 78%,transparent); backdrop-filter:blur(3px); pointer-events:none; }
.page-drop-overlay__panel { max-width:760px; width:min(760px,90vw); border:3px dashed var(--accent); box-shadow:10px 10px 0 var(--shadow); background:var(--card); color:var(--ink); padding:2rem; text-align:center; font-weight:700; }
.page-drop-overlay__panel small { display:block; opacity:.72; margin-top:.6rem; font-weight:500; }
body.page-drag-over .page-drop-overlay { display:flex; }
.queue-table { overflow-x:auto; border:1px solid var(--line); background:var(--card); }
.queue-row { min-width:1360px; display:grid; grid-template-columns:1.7fr .8fr .75fr .6fr .65fr .8fr .65fr .8fr .8fr .9fr 1.15fr .75fr 44px; align-items:center; gap:.45rem; padding:.45rem .6rem; border-bottom:1px solid var(--line); }
.queue-head { background:var(--ink); color:white; font-family:'DM Mono',monospace; font-size:.7rem; letter-spacing:.04em; }
.queue-row:last-child { border-bottom:none; }
.edit-config-panel { width:100%; overflow-x:auto; border:1px solid #d1d5db; padding:.75rem; }
.edit-config-line { min-width:1900px; display:flex; flex-wrap:nowrap; gap:.75rem; align-items:end; }
.edit-config-label { flex:0 0 360px; align-self:center; }
.edit-config-line .q-field, .edit-config-line .q-btn { flex:0 0 auto; }
.edit-field-mode { width:150px; }
.edit-field-format { width:310px; }
.edit-field-preset { width:165px; }
.edit-field-short { width:145px; }
.edit-field-bitrate { width:185px; }
.edit-field-output { width:220px; }
.edit-config-action { width:170px; height:56px; min-height:56px; align-self:end; }
.edit-config-action .q-btn__content { flex-wrap:nowrap; white-space:nowrap; }
</style>''', shared=True)


@ui.page("/")
def index() -> None:
    selected_files: list[dict[str, object]] = []
    output_directory = {"path": resolve_output_directory(None)}
    selected_queue_item: dict[str, Optional[dict[str, object]]] = {"item": None}
    max_parallel_jobs = min(8, max(1, os.cpu_count() or 1))
    dark_mode = ui.dark_mode(value=False)

    with ui.column().classes("w-full min-h-screen items-center hero-grid px-5 py-10"):
        with ui.column().classes("w-full max-w-none gap-7"):
            with ui.row().classes("w-full items-start justify-between gap-4 flex-wrap"):
                with ui.column().classes("gap-1"):
                    ui.label("LOCAL VIDEO LAB").classes("mono text-sm font-bold")
                    ui.label("压缩一下").classes("text-4xl md:text-6xl font-bold leading-tight")

                def switch_dark_mode(event) -> None:
                    dark_mode.enable() if event.value else dark_mode.disable()

                ui.switch("黑暗模式", value=False, on_change=switch_dark_mode).props("dense color=primary").classes("official-theme-switch")
            ui.label("PyAV 驱动的离线视频工作台。文件仅在本机处理，成品可保存到指定目录。").classes("text-lg max-w-2xl")

            with ui.card().classes("tool-card w-full p-5 md:p-8 rounded-none"):
                ui.label("01 / 输入视频（可多选）").classes("mono font-bold")

                def render_queue() -> None:
                    queue_label.set_text(f"待转换队列：{len(selected_files)} 个文件")
                    rows = [{
                        "id": str(item["path"]),
                        "file": f"{item['name']} ({item['size'] / 1024 / 1024:.1f} MB)",
                        "codec": item["codec"], "dimensions": item["dimensions"], "fps": item["fps"],
                        "duration": item["duration"], "bitrate": item["bitrate"], "crf": item["target_crf"],
                        "width": item["target_width"] or "保持", "target_fps": item["target_fps"] or "保持",
                        "target_bitrate": item["target_bitrate"] or "CRF", "config_mode": item["config_mode"],
                        "format": item["target_format"] if item["config_mode"] == "单独配置" else "共用配置",
                        "output": normalise_output_mode(item["output_mode"]),
                        "status": item.get("status", "待转换"),
                    } for item in selected_files]
                    queue_table.update_rows(rows)

                def remove_from_queue(item: dict[str, object]) -> None:
                    selected_files.remove(item)
                    if item.get("is_temporary"):
                        Path(item["path"]).unlink(missing_ok=True)
                    render_queue()

                def clear_queue() -> None:
                    for item in selected_files:
                        if item.get("is_temporary"):
                            Path(item["path"]).unlink(missing_ok=True)
                    selected_files.clear()
                    render_queue()

                async def add_local_paths(paths: list[Path]) -> None:
                    """读取本地原始路径的元数据并加入队列，不复制大文件。"""
                    existing_paths = {Path(item["path"]).resolve() for item in selected_files}
                    new_paths = [path for path in paths if path not in existing_paths]
                    skipped = len(paths) - len(new_paths)
                    failures: list[str] = []
                    for path in new_paths:
                        try:
                            metadata = await run.io_bound(probe_video, path)
                        except Exception as exc:
                            failures.append(f"{path.name}：{exc}")
                            continue
                        selected_files.append({
                            "path": path, "name": path.name, "size": path.stat().st_size, **metadata,
                            "target_crf": crf_input.value, "target_width": width_input.value, "target_fps": fps_input.value,
                            "target_bitrate": bitrate_input.value, "target_format": format_select.value,
                            "target_preset": preset_select.value, "config_mode": "单独配置",
                            "output_mode": SOURCE_OUTPUT_MODE, "status": "待转换",
                            "is_temporary": False, "source_directory": path.parent,
                        })
                    render_queue()
                    if new_paths:
                        ui.notify(f"已加入 {len(new_paths) - len(failures)} 个本地视频文件" + (f"，跳过重复 {skipped} 个" if skipped else ""), type="positive")
                    if failures:
                        ui.notify("以下文件无法解析：\n" + "\n".join(failures), type="warning", close_button=True, timeout=0)

                async def select_local_files() -> None:
                    paths = await run.io_bound(choose_input_files, Path.home())
                    await add_local_paths(paths)

                async def select_local_directory() -> None:
                    paths = await run.io_bound(choose_input_directory, Path.home())
                    if not paths:
                        ui.notify("所选文件夹中没有找到支持的视频文件。", type="warning")
                        return
                    await add_local_paths(paths)

                async def enrich_uploaded_item(item: dict[str, object]) -> None:
                    """在文件已显示在队列后，异步补充 PyAV 媒体信息。"""
                    try:
                        metadata = await run.io_bound(probe_video, Path(item["path"]))
                    except Exception as exc:
                        item.update(codec="无法读取", dimensions="—", fps="—", duration="—", bitrate="—", status="解析失败")
                        ui.notify(f"无法读取 {item['name']} 的视频信息：{exc}", type="negative", close_button=True)
                    else:
                        item.update(**metadata, status="待转换")
                    render_queue()

                def receive_upload(event) -> None:
                    """在上传请求中立即复制文件，避免异步任务读取到已关闭的浏览器临时流。"""
                    file_path: Optional[Path] = None
                    try:
                        upload_file = getattr(event, "file", None)
                        file_name = (getattr(event, "name", None)
                                     or getattr(upload_file, "filename", None)
                                     or getattr(upload_file, "name", None))
                        content = getattr(event, "content", None) or getattr(upload_file, "content", None)
                        if not file_name or content is None:
                            raise ValueError("上传组件未提供文件内容。")
                        suffix = Path(str(file_name)).suffix or ".video"
                        file_path = WORK_DIR / f"{uuid.uuid4().hex}{suffix}"
                        file_path.write_bytes(content.read())
                        file_size = file_path.stat().st_size
                        if file_size == 0:
                            raise ValueError("上传的文件为空。")
                    except Exception as exc:
                        if file_path is not None:
                            file_path.unlink(missing_ok=True)
                        ui.notify(f"无法接收所选文件：{exc}", type="negative", close_button=True)
                        return

                    item: dict[str, object] = {
                        "path": file_path, "name": str(file_name), "size": file_size,
                        "codec": "读取中…", "dimensions": "读取中…", "fps": "—", "duration": "—", "bitrate": "—",
                        "source_width": None, "source_height": None, "source_fps": None, "source_bitrate_kbps": None,
                        "target_crf": crf_input.value, "target_width": width_input.value, "target_fps": fps_input.value,
                        "target_bitrate": bitrate_input.value, "target_format": format_select.value,
                        "target_preset": preset_select.value, "config_mode": "单独配置",
                        "output_mode": CUSTOM_OUTPUT_MODE, "status": "读取文件信息…",
                        "is_temporary": True, "source_directory": None,
                    }
                    selected_files.append(item)
                    render_queue()
                    ui.notify(f"已加入表格：{file_name}", type="positive")
                    background_tasks.create(enrich_uploaded_item(item), name=f"probe_{file_path.stem}")

                upload_component = ui.upload(on_upload=receive_upload, multiple=True, auto_upload=True, max_file_size=4 * 1024 ** 3, max_files=50).props('accept="video/*"').classes("hidden")
                upload_url = upload_component._props["url"]
                ui.html('<div id="video-page-drop-overlay" class="page-drop-overlay"><div class="page-drop-overlay__panel">松手添加视频文件或文件夹<small id="video-page-drop-status">整页都支持拖放；Chrome / Edge 可递归遍历文件夹内所有常见视频文件。</small></div></div>')
                ui.add_body_html(f'''<script>
(function(){{
  if (window.videoLabPageDropBound === '1') return;
  window.videoLabPageDropBound = '1';

  const uploadUrl = {json.dumps(upload_url)};
  const videoExtensions = new Set({json.dumps(sorted(VIDEO_EXTENSIONS))});
  let dragDepth = 0;

  function statusElement() {{
    return document.getElementById('video-page-drop-status');
  }}

  function setStatus(message) {{
    const status = statusElement();
    if (status) status.textContent = message;
  }}

  function hasFiles(event) {{
    const types = Array.from((event.dataTransfer && event.dataTransfer.types) || []);
    return types.includes('Files');
  }}

  function setDragState(active) {{
    document.body.classList.toggle('page-drag-over', active);
    if (active) setStatus('松手后会递归扫描并上传支持的视频文件。');
  }}

  function isVideoFile(file) {{
    const name = (file.name || '').toLowerCase();
    return Array.from(videoExtensions).some(ext => name.endsWith(ext));
  }}

  function readEntry(entry, path = '') {{
    return new Promise(resolve => {{
      if (entry.isFile) {{
        entry.file(file => {{
          file.relativePath = path + file.name;
          resolve([file]);
        }}, () => resolve([]));
        return;
      }}

      if (entry.isDirectory) {{
        const reader = entry.createReader();
        let entries = [];

        function readBatch() {{
          reader.readEntries(batch => {{
            if (!batch.length) {{
              Promise.all(entries.map(child => readEntry(child, path + entry.name + '/')))
                .then(groups => resolve(groups.flat()));
              return;
            }}
            entries = entries.concat(Array.from(batch));
            readBatch();
          }}, () => resolve([]));
        }}

        readBatch();
        return;
      }}

      resolve([]);
    }});
  }}

  async function collectFiles(dataTransfer) {{
    const items = Array.from(dataTransfer.items || []);
    if (items.length && items.some(item => typeof item.webkitGetAsEntry === 'function')) {{
      const groups = await Promise.all(items.map(item => {{
        const entry = item.webkitGetAsEntry();
        return entry ? readEntry(entry) : Promise.resolve([]);
      }}));
      return groups.flat().filter(isVideoFile);
    }}
    return Array.from(dataTransfer.files || []).filter(isVideoFile);
  }}

  async function uploadFiles(files) {{
    if (!files.length) {{
      setStatus('没有找到支持的视频文件。');
      setTimeout(() => setDragState(false), 900);
      return;
    }}

    document.body.classList.add('page-drag-over');
    setStatus(`发现 ${{files.length}} 个视频，正在上传...`);
    let ok = 0;

    for (const file of files) {{
      const form = new FormData();
      form.append('file', file, file.relativePath || file.webkitRelativePath || file.name);
      try {{
        const response = await fetch(uploadUrl, {{ method: 'POST', body: form }});
        if (response.ok) ok += 1;
      }} catch (error) {{
        console.error(error);
      }}
      setStatus(`已上传 ${{ok}} / ${{files.length}} 个视频...`);
    }}

    setStatus(`上传完成：${{ok}} / ${{files.length}} 个视频已加入表格。`);
    setTimeout(() => setDragState(false), 1100);
  }}

  window.addEventListener('dragenter', event => {{
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    setDragState(true);
  }});

  window.addEventListener('dragover', event => {{
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setDragState(true);
  }});

  window.addEventListener('dragleave', event => {{
    if (!hasFiles(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) setDragState(false);
  }});

  window.addEventListener('drop', async event => {{
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;

    try {{
      const files = await collectFiles(event.dataTransfer);
      await uploadFiles(files);
    }} catch (error) {{
      console.error(error);
      setStatus('递归解析拖放内容失败，请改用“选择文件夹并扫描”。');
      setTimeout(() => setDragState(false), 1600);
    }}
  }});
}})();
</script>''')
                with ui.row().classes("w-full items-center gap-3"):
                    ui.button("从本机选择文件", icon="video_library", on_click=select_local_files).props("outline dense")
                    ui.button("选择文件夹并扫描", icon="folder", on_click=select_local_directory).props("outline dense")
                    ui.label("整页都可拖入文件和文件夹；拖放上传默认保存到自定义输出目录。本机选择文件/目录可保存到原文件夹。").classes("text-xs text-gray-600")
                    ui.button("清空待转换队列", icon="delete_sweep", on_click=clear_queue).props("flat dense")

                ui.separator().classes("my-5")
                ui.label("02 / 已添加文件（拖入后自动显示于此表格）").classes("mono font-bold")
                queue_label = ui.label("待转换队列：0 个文件").classes("text-lg font-medium mt-2")
                table_columns = [
                    {"name": "file", "label": "文件名 / 大小", "field": "file", "align": "left", "sortable": True},
                    {"name": "codec", "label": "编码", "field": "codec"},
                    {"name": "dimensions", "label": "源尺寸", "field": "dimensions"},
                    {"name": "fps", "label": "源 FPS", "field": "fps"},
                    {"name": "duration", "label": "时长", "field": "duration"},
                    {"name": "bitrate", "label": "源码率", "field": "bitrate"},
                    {"name": "crf", "label": "目标 CRF", "field": "crf"},
                    {"name": "width", "label": "目标宽度", "field": "width"},
                    {"name": "target_fps", "label": "目标 FPS", "field": "target_fps"},
                    {"name": "target_bitrate", "label": "目标码率 kbps", "field": "target_bitrate"},
                    {"name": "config_mode", "label": "配置模式", "field": "config_mode"},
                    {"name": "format", "label": "输出格式", "field": "format"},
                    {"name": "output", "label": "输出位置", "field": "output"},
                    {"name": "status", "label": "任务状态", "field": "status"},
                ]

                def select_table_row(event) -> None:
                    selected_rows = event.selection
                    if not selected_rows:
                        return
                    selected_id = selected_rows[0]["id"]
                    item = next((candidate for candidate in selected_files if str(candidate["path"]) == selected_id), None)
                    if item is None:
                        return
                    selected_queue_item["item"] = item
                    selected_file_label.set_text(f"正在编辑：{item['name']}（宽度 / FPS / 码率为空时已自动回填源视频当前值）")
                    row_crf.set_value(item["target_crf"])
                    row_width.set_value(item["target_width"] or item.get("source_width"))
                    row_fps.set_value(item["target_fps"] or item.get("source_fps"))
                    row_bitrate.set_value(item["target_bitrate"] or item.get("source_bitrate_kbps"))
                    row_config_mode.set_value(item["config_mode"])
                    row_format.set_value(item["target_format"])
                    row_preset.set_value(item["target_preset"])
                    row_output.set_value(normalise_output_mode(item["output_mode"]))

                queue_table = ui.table(columns=table_columns, rows=[], row_key="id", selection="single", on_select=select_table_row, pagination={"rowsPerPage": 10}).classes("w-full my-3").props("dense flat bordered separator=cell")
                with ui.element("div").classes("edit-config-panel"):
                    with ui.element("div").classes("edit-config-line") as edit_config_line:
                        selected_file_label = ui.label("点击表格中的一行后，在右侧单行编辑全部参数。").classes("edit-config-label text-sm")
                        row_config_mode = ui.select({"共用配置": "共用配置", "单独配置": "单独配置"}, value="单独配置", label="配置模式").classes("edit-field-mode")
                        row_format = ui.select(list(FORMAT_SETTINGS), label="单独输出格式").classes("edit-field-format")
                        row_preset = ui.select(["ultrafast", "fast", "medium", "slow"], label="单独编码预设").classes("edit-field-preset")
                        row_crf = ui.select(CRF_CHOICES, label="目标 CRF", with_input=True, new_value_mode="add-unique").classes("edit-field-short")
                        row_width = ui.select(WIDTH_CHOICES, label="目标宽度", with_input=True, new_value_mode="add-unique", clearable=True).classes("edit-field-short")
                        row_fps = ui.select(FPS_CHOICES, label="目标 FPS", with_input=True, new_value_mode="add-unique", clearable=True).classes("edit-field-short")
                        row_bitrate = ui.select(BITRATE_CHOICES, label="目标码率 kbps", with_input=True, new_value_mode="add-unique", clearable=True).classes("edit-field-bitrate")

                        def sync_row_output_mode(event) -> None:
                            item = selected_queue_item["item"]
                            if item is None:
                                return
                            selected_output_mode = normalise_output_mode(event.value)
                            if selected_output_mode == SOURCE_OUTPUT_MODE and item.get("source_directory") is None:
                                selected_output_mode = CUSTOM_OUTPUT_MODE
                                row_output.set_value(CUSTOM_OUTPUT_MODE)
                                ui.notify("拖放/网页上传的文件无法读取原始目录，已自动改为“自定义输出目录”。如需保存到原文件夹，请使用“从本机选择文件”或“选择文件夹并扫描”。", type="warning", close_button=True)
                            item["output_mode"] = selected_output_mode
                            render_queue()

                        row_output = ui.select(OUTPUT_MODE_OPTIONS, value=CUSTOM_OUTPUT_MODE, label="输出位置", on_change=sync_row_output_mode).classes("edit-field-output")

                    def apply_row_settings() -> None:
                        item = selected_queue_item["item"]
                        if item is None:
                            ui.notify("请先点击表格中的文件行。", type="warning")
                            return
                        selected_output_mode = normalise_output_mode(row_output.value)
                        if selected_output_mode == SOURCE_OUTPUT_MODE and item.get("source_directory") is None:
                            selected_output_mode = CUSTOM_OUTPUT_MODE
                            row_output.set_value(CUSTOM_OUTPUT_MODE)
                            ui.notify("拖放/网页上传的文件无法读取原始目录，已自动改为“自定义输出目录”。如需保存到原文件夹，请使用“从本机选择文件”或“选择文件夹并扫描”。", type="warning", close_button=True)
                        item.update(config_mode=row_config_mode.value, target_format=row_format.value,
                                    target_preset=row_preset.value, target_crf=row_crf.value,
                                    target_width=row_width.value, target_fps=row_fps.value,
                                    target_bitrate=row_bitrate.value, output_mode=selected_output_mode)
                        render_queue()

                    def remove_selected_row() -> None:
                        item = selected_queue_item["item"]
                        if item is None:
                            ui.notify("请先点击表格中的文件行。", type="warning")
                            return
                        selected_queue_item["item"] = None
                        remove_from_queue(item)
                        selected_file_label.set_text("已移除文件。")

                    with edit_config_line:
                        ui.button("保存本行设置", icon="save", on_click=apply_row_settings).props("outline").classes("edit-config-action")
                        ui.button("移除选中行", icon="delete", on_click=remove_selected_row).props("outline color=negative").classes("edit-config-action")
                render_queue()

                ui.separator().classes("my-5")
                ui.label("03 / 共用批量配置").classes("mono font-bold")
                ui.label("选择“共用配置”的视频会在开始转换时共同使用这里的格式、预设和目标参数；单独配置的视频不受此处修改影响。").classes("text-sm font-medium")
                with ui.card().classes("format-guide w-full rounded-none mt-2"):
                    with ui.card_section().classes("py-3"):
                        ui.label("输出格式速览").classes("mono font-bold text-sm")
                        ui.label("<strong>MP4 H.264</strong>：通用兼容；<strong>MP4 / MKV H.265</strong>：更小体积；<strong>MOV / M4V</strong>：剪辑与 Apple 设备；<strong>WebM VP9</strong>：网页播放；<strong>TS / FLV</strong>：流媒体兼容。") \
                            .props("innerHTML").classes("text-sm")
                with ui.grid(columns=2).classes("w-full gap-4"):
                    format_select = ui.select(list(FORMAT_SETTINGS), value="MP4 (H.264 + AAC，通用兼容)", label="目标格式（适用于全部文件）")
                    crf_input = ui.select(CRF_CHOICES, value=23, label="默认 CRF（可选常用值或手输）", with_input=True, new_value_mode="add-unique")
                    preset_select = ui.select(["ultrafast", "fast", "medium", "slow"], value="medium", label="编码预设")
                    width_input = ui.select(WIDTH_CHOICES, label="默认最大宽度（可选常用值或手输）", with_input=True, new_value_mode="add-unique", clearable=True)
                    fps_input = ui.select(FPS_CHOICES, label="默认输出 FPS（可选常用值或手输）", with_input=True, new_value_mode="add-unique", clearable=True)
                    bitrate_input = ui.select(BITRATE_CHOICES, label="默认目标码率 kbps（留空使用 CRF）", with_input=True, new_value_mode="add-unique", clearable=True)
                    parallelism_select = ui.select(
                        {count: f"{count} 个并行进程" for count in range(1, max_parallel_jobs + 1)},
                        value=max_parallel_jobs,
                        label="批量并行数",
                    )

                def set_all_to_shared_config() -> None:
                    for item in selected_files:
                        item["config_mode"] = "共用配置"
                    render_queue()
                    ui.notify(f"已将 {len(selected_files)} 个文件切换为共用配置。", type="positive")

                ui.button("全部文件使用共用配置", icon="format_paint", on_click=set_all_to_shared_config).props("color=primary").classes("mt-2")
                with ui.row().classes("w-full items-center gap-3 mt-4"):
                    output_dir_label = ui.label(f"自定义输出目录：{output_directory['path']}").classes("text-sm break-all flex-grow")

                    async def select_output_directory() -> None:
                        selected = await run.io_bound(choose_output_directory, output_directory["path"])
                        if selected is not None:
                            output_directory["path"] = selected
                            output_dir_label.set_text(f"自定义输出目录：{selected}")

                    ui.button("选择自定义输出目录", icon="folder_open", on_click=select_output_directory).props("outline")

                def set_all_output_mode(mode: str) -> None:
                    if not selected_files:
                        ui.notify("队列里还没有文件。", type="warning")
                        return
                    changed = 0
                    fallback = 0
                    for item in selected_files:
                        target_mode = mode
                        if target_mode == SOURCE_OUTPUT_MODE and item.get("source_directory") is None:
                            target_mode = CUSTOM_OUTPUT_MODE
                            fallback += 1
                        item["output_mode"] = target_mode
                        changed += 1
                    render_queue()
                    if selected_queue_item["item"] is not None:
                        row_output.set_value(normalise_output_mode(selected_queue_item["item"].get("output_mode")))
                    if mode == SOURCE_OUTPUT_MODE and fallback:
                        ui.notify(f"已更新 {changed} 个文件；其中 {fallback} 个拖放/上传文件无法定位原文件夹，保留为自定义输出目录。", type="warning", close_button=True)
                    else:
                        ui.notify(f"已将 {changed} 个文件设置为“{mode}”。", type="positive")

                with ui.row().classes("w-full items-center gap-3 mt-2"):
                    ui.button("全部输出到自定义目录", icon="drive_file_move", on_click=lambda: set_all_output_mode(CUSTOM_OUTPUT_MODE)).props("outline dense")
                    ui.button("全部输出到原文件夹", icon="folder_copy", on_click=lambda: set_all_output_mode(SOURCE_OUTPUT_MODE)).props("outline dense")
                    ui.label("单行下拉会立即生效；批量按钮可一次性覆盖队列输出位置。拖放上传文件因浏览器限制不能获取真实原文件夹。").classes("text-xs text-gray-600")

                status = ui.label("等待任务").classes("mono text-sm mt-6")

                async def start_conversion() -> None:
                    if not selected_files:
                        ui.notify("请先选择至少一个视频文件。", type="warning")
                        return
                    try:
                        output_dir = resolve_output_directory(output_directory["path"])
                    except (TypeError, ValueError) as exc:
                        ui.notify(str(exc), type="negative")
                        return

                    button.disable()
                    spinner = ui.spinner("dots", size="lg").classes("mt-2")
                    completed: list[Path] = []
                    failures: list[str] = []
                    try:
                        jobs: list[tuple] = []
                        for index, input_file in enumerate(list(selected_files), start=1):
                            try:
                                if input_file["config_mode"] == "共用配置":
                                    file_format = format_select.value
                                    file_preset = preset_select.value
                                else:
                                    file_format = input_file["target_format"]
                                    file_preset = input_file["target_preset"]
                                output_extension = FORMAT_SETTINGS[file_format]["extension"]
                                file_output_dir, final_output_mode, fell_back_to_custom = choose_effective_output_directory(
                                    input_file.get("output_mode"), output_dir, input_file.get("source_directory"),
                                )
                                input_file["output_mode"] = final_output_mode
                                if fell_back_to_custom:
                                    input_file["status"] = "已改用自定义输出目录"
                                output_path = build_available_output_path(file_output_dir, str(input_file["name"]), output_extension)
                                file_crf = parse_crf(input_file["target_crf"])
                                file_width = parse_optional_positive_int(input_file["target_width"], "目标宽度")
                                file_fps = parse_optional_positive_int(input_file["target_fps"], "目标帧率")
                                file_bitrate = parse_optional_positive_int(input_file["target_bitrate"], "目标码率")
                                if file_width and file_width < 2:
                                    raise ValueError("目标宽度至少为 2。")
                            except Exception as exc:
                                input_file["status"] = "参数错误"
                                failures.append(f"{input_file['name']}：{exc}")
                            else:
                                input_file["status"] = "排队中"
                                jobs.append((index, input_file, output_path, file_format, file_crf,
                                             file_preset, file_width, file_fps, file_bitrate))
                        render_queue()
                        parallelism = int(parallelism_select.value)
                        status.set_text(f"已提交 {len(jobs)} 个任务，使用 {parallelism} 个进程并行转换…")
                        semaphore = asyncio.Semaphore(parallelism)
                        loop = asyncio.get_running_loop()

                        async def process_job(job: tuple) -> None:
                            (index, input_file, output_path, file_format, file_crf,
                             file_preset, file_width, file_fps, file_bitrate) = job
                            async with semaphore:
                                input_file["status"] = "转换中"
                                status.set_text(f"正在转换 {index}/{len(selected_files)}：{input_file['name']}（并行 {parallelism}）")
                                render_queue()
                                try:
                                    await loop.run_in_executor(
                                        executor, convert_video, str(input_file["path"]), str(output_path), file_format,
                                        file_crf, file_preset, file_width, file_fps, file_bitrate,
                                    )
                                except Exception as exc:
                                    output_path.unlink(missing_ok=True)
                                    input_file["status"] = "失败"
                                    failures.append(f"{input_file['name']}：{exc}")
                                else:
                                    input_file["status"] = f"完成：{output_path.name}"
                                    completed.append(output_path)
                                finally:
                                    render_queue()

                        with ProcessPoolExecutor(max_workers=parallelism) as executor:
                            await asyncio.gather(*(process_job(job) for job in jobs))
                        if failures:
                            status.set_text(f"批量处理完成：成功 {len(completed)} 个，失败 {len(failures)} 个。请查看每行的输出位置设置。")
                            ui.notify("部分文件未能转换：\n" + "\n".join(failures), type="warning", close_button=True, timeout=0)
                        else:
                            status.set_text(f"批量处理完成：成功 {len(completed)} 个。文件已保存到每行设置的输出位置。")
                    finally:
                        spinner.delete()
                        button.enable()

                button = ui.button("开始压缩 / 转换", icon="bolt", on_click=start_conversion).props("color=primary").classes("mt-4")

            ui.label("提示：CRF 越小画质越高、文件通常越大。推荐先用 MP4 / CRF 23 / medium。").classes("mono text-xs")


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Local Video Lab",
           host="127.0.0.1",
           port=9297,
           reload=False,
           language="zh-CN",
           # native=True,
           )
