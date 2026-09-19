"""本地视频压缩与格式转换工具（NiceGUI + PyAV）。"""
from __future__ import annotations

import asyncio
import atexit
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

# 浏览器上传文件的临时工作目录（本地处理的副本都放在这里）
WORK_DIR = Path(tempfile.mkdtemp(prefix="video_converter_"))
# 程序退出时清理临时目录，避免残留大文件
atexit.register(lambda: shutil.rmtree(WORK_DIR, ignore_errors=True))


async def shutdown_after_last_client_disconnect() -> None:
    """关闭最后一个浏览器页面后，延迟确认无客户端连接再退出本地服务。"""
    # 等待一小段时间，避免页面刷新、重连或多标签切换时误判为全部断开
    await asyncio.sleep(2)
    # 清理已失效的陈旧客户端（NiceGUI 3.x 已无 auto-index 客户端，instances 只保存真实页面客户端）
    Client.prune_instances()
    # 只要还有任意一个客户端保持 socket 连接，就继续保持本地服务运行
    connected = any(client.has_socket_connection for client in Client.instances.values())
    if not connected:
        # 所有页面均已关闭，停止本地服务并结束进程（atexit 会清理上传临时目录）
        app.shutdown()


# 绑定“最后一个页面关闭”事件：页面断开后由上面的回调确认无连接再自动停止服务
app.on_disconnect(shutdown_after_last_client_disconnect)

# 输出格式映射：键为界面下拉文案，值给出扩展名、封装容器与视频/音频编码器
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
# 支持的输入视频扩展名（用于文件夹递归扫描与拖放筛选）
VIDEO_EXTENSIONS = {".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv"}
# 下拉常用值：CRF、目标宽度、目标帧率、目标码率（kbps）
CRF_CHOICES = [16, 18, 20, 22, 23, 24, 26, 28, 30, 32, 33, 35, 38, 40, 45]
WIDTH_CHOICES = [320, 426, 480, 640, 720, 854, 960, 1024, 1280, 1366, 1600, 1920, 2048, 2560, 3840]
FPS_CHOICES = [15, 18, 20, 23, 24, 25, 29, 30, 48, 50, 60, 90, 120]
BITRATE_CHOICES = [300, 500, 800, 1000, 1500, 2000, 2500, 3500, 4000, 5000, 6000, 8000, 12000, 16000, 20000, 35000, 50000]
# 参数预设：未套用预设时的默认选项文案
PARAMETER_PRESET_CUSTOM = "自定义 / 不套用预设"
# 一键参数预设组：按目标尺寸聚合，值为 CRF / 宽度 / FPS / 码率 / 编码预设
PARAMETER_PRESETS = {
    "【原始】原画质保留（保持宽度 / 保持 FPS / CRF 18）": {"crf": 18, "width": None, "fps": None, "bitrate": None, "preset": "slow"},
    "【原始】高质量轻压（保持宽度 / 保持 FPS / CRF 20）": {"crf": 20, "width": None, "fps": None, "bitrate": None, "preset": "medium"},
    "【原始】快速压缩（保持宽度 / 保持 FPS / CRF 26）": {"crf": 26, "width": None, "fps": None, "bitrate": None, "preset": "fast"},
    "【4K】高质量归档（2160P / 60 FPS / CRF 18）": {"crf": 18, "width": 3840, "fps": 60, "bitrate": None, "preset": "slow"},
    "【4K】通用观看（2160P / 30 FPS / CRF 22）": {"crf": 22, "width": 3840, "fps": 30, "bitrate": None, "preset": "medium"},
    "【4K】省空间（2160P / 30 FPS / CRF 26）": {"crf": 26, "width": 3840, "fps": 30, "bitrate": None, "preset": "fast"},
    "【4K】固定码率（2160P / 30 FPS / 35000 kbps）": {"crf": 23, "width": 3840, "fps": 30, "bitrate": 35000, "preset": "medium"},
    "【2K】高质量归档（1440P / 60 FPS / CRF 20）": {"crf": 20, "width": 2560, "fps": 60, "bitrate": None, "preset": "slow"},
    "【2K】通用推荐（1440P / 30 FPS / CRF 23）": {"crf": 23, "width": 2560, "fps": 30, "bitrate": None, "preset": "medium"},
    "【2K】省空间（1440P / 30 FPS / CRF 28）": {"crf": 28, "width": 2560, "fps": 30, "bitrate": None, "preset": "fast"},
    "【2K】固定码率（1440P / 30 FPS / 16000 kbps）": {"crf": 23, "width": 2560, "fps": 30, "bitrate": 16000, "preset": "medium"},
    "【1080P】高质量分享（1080P / 60 FPS / CRF 20）": {"crf": 20, "width": 1920, "fps": 60, "bitrate": None, "preset": "medium"},
    "【1080P】通用推荐（1080P / 30 FPS / CRF 23）": {"crf": 23, "width": 1920, "fps": 30, "bitrate": None, "preset": "medium"},
    "【1080P】快速压缩（1080P / 30 FPS / CRF 26）": {"crf": 26, "width": 1920, "fps": 30, "bitrate": None, "preset": "fast"},
    "【1080P】省空间（1080P / 24 FPS / CRF 30）": {"crf": 30, "width": 1920, "fps": 24, "bitrate": None, "preset": "fast"},
    "【1080P】固定码率（30 FPS / 6000 kbps）": {"crf": 23, "width": 1920, "fps": 30, "bitrate": 6000, "preset": "medium"},
    "【1080P】固定码率（60 FPS / 12000 kbps）": {"crf": 23, "width": 1920, "fps": 60, "bitrate": 12000, "preset": "medium"},
    "【900P】窗口录屏（1600 宽 / 30 FPS / CRF 24）": {"crf": 24, "width": 1600, "fps": 30, "bitrate": None, "preset": "medium"},
    "【720P】移动端流畅（720P / 60 FPS / CRF 26）": {"crf": 26, "width": 1280, "fps": 60, "bitrate": None, "preset": "medium"},
    "【720P】通用分享（720P / 30 FPS / CRF 28）": {"crf": 28, "width": 1280, "fps": 30, "bitrate": None, "preset": "fast"},
    "【720P】会议课件（720P / 25 FPS / CRF 30）": {"crf": 30, "width": 1280, "fps": 25, "bitrate": None, "preset": "fast"},
    "【720P】极小文件（720P / 24 FPS / CRF 33）": {"crf": 33, "width": 1280, "fps": 24, "bitrate": None, "preset": "fast"},
    "【720P】固定码率（30 FPS / 2500 kbps）": {"crf": 23, "width": 1280, "fps": 30, "bitrate": 2500, "preset": "medium"},
    "【540P】网页嵌入（960 宽 / 30 FPS / CRF 30）": {"crf": 30, "width": 960, "fps": 30, "bitrate": None, "preset": "medium"},
    "【540P】聊天发送（960 宽 / 24 FPS / CRF 33）": {"crf": 33, "width": 960, "fps": 24, "bitrate": None, "preset": "fast"},
    "【480P】标准压缩（480P / 30 FPS / CRF 32）": {"crf": 32, "width": 854, "fps": 30, "bitrate": None, "preset": "fast"},
    "【480P】极小体积（480P / 24 FPS / CRF 35）": {"crf": 35, "width": 854, "fps": 24, "bitrate": None, "preset": "fast"},
    "【480P】固定码率（30 FPS / 1000 kbps）": {"crf": 28, "width": 854, "fps": 30, "bitrate": 1000, "preset": "fast"},
    "【360P】预览小样（360P / 24 FPS / CRF 38）": {"crf": 38, "width": 640, "fps": 24, "bitrate": None, "preset": "ultrafast"},
    "【240P】极速预览（240P / 15 FPS / CRF 40）": {"crf": 40, "width": 426, "fps": 15, "bitrate": None, "preset": "ultrafast"},
}
# 输出位置模式：保存到自定义目录，或保存到原文件夹
CUSTOM_OUTPUT_MODE = "自定义输出目录"
SOURCE_OUTPUT_MODE = "原文件夹输出"
# 下拉选项（值与显示一致），以及旧版文案到新文案的兼容映射
OUTPUT_MODE_OPTIONS = {CUSTOM_OUTPUT_MODE: CUSTOM_OUTPUT_MODE, SOURCE_OUTPUT_MODE: SOURCE_OUTPUT_MODE}
LEGACY_OUTPUT_MODE_MAP = {"统一目录": CUSTOM_OUTPUT_MODE, "源文件所在目录": SOURCE_OUTPUT_MODE}
# 未做任何设置时的默认方案：默认输出格式 + “原始 / 原画质保留”参数预设（保持源宽度与源帧率）
DEFAULT_TARGET_FORMAT = "MP4 (H.264 + AAC，通用兼容)"
DEFAULT_PARAMETER_PRESET = "【原始】原画质保留（保持宽度 / 保持 FPS / CRF 18）"


def default_target_settings() -> dict[str, object]:
    """新加入文件与编辑面板的初始目标参数，取自默认参数预设。"""
    preset = PARAMETER_PRESETS[DEFAULT_PARAMETER_PRESET]
    return {
        "target_format": DEFAULT_TARGET_FORMAT,
        "target_preset": preset["preset"],
        "target_crf": preset["crf"],
        "target_width": preset["width"],
        "target_fps": preset["fps"],
        "target_bitrate": preset["bitrate"],
    }


def normalise_even(value: int) -> int:
    """返回适合 yuv420p 的、至少为 2 的偶数尺寸。"""
    return max(2, value - value % 2)


def parse_optional_positive_int(value: object, label: str) -> Optional[int]:
    """解析可留空的整数参数：留空返回 None，非法值抛出带参数名的中文错误。"""
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
    """校验 CRF 是否为 0 到 51 的整数。"""
    try:
        crf = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("CRF 必须是 0 到 51 的整数。") from exc
    if not 0 <= crf <= 51:
        raise ValueError("CRF 必须在 0 到 51 之间。")
    return crf


def format_duration(seconds: Optional[float]) -> str:
    """把秒数格式化为 HH:MM:SS 或 MM:SS，无有效时长时显示占位符。"""
    if seconds is None or seconds < 0:
        return "—"
    total_seconds = round(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def format_bitrate(bits_per_second: Optional[int]) -> str:
    """把码率按 Mbps / kbps 自适应显示，无有效码率时显示占位符。"""
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
    """按“原文件名_converted.新扩展名”生成输出文件名。"""
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
    """按最大宽度等比缩放并统一取偶数尺寸；不缩放时仅做偶数处理。"""
    if not max_width or max_width >= source_width:
        return normalise_even(source_width), normalise_even(source_height)
    height = round(source_height * max_width / source_width)
    return normalise_even(max_width), normalise_even(height)


def configure_video_stream(container: av.container.OutputContainer, settings: dict, width: int, height: int,
                           rate: object, crf: int, preset: str, target_bitrate_kbps: Optional[int]) -> av.video.stream.VideoStream:
    """创建输出视频流，并按编码器类型使用码率模式或 CRF 质量模式。"""
    stream = container.add_stream(settings["video"], rate=rate)
    stream.width, stream.height = width, height
    # yuv420p 兼容性最好，但要求宽高均为偶数
    stream.pix_fmt = "yuv420p"
    if target_bitrate_kbps:
        # 填写了目标码率时优先按指定码率编码（CRF 仅作为界面保留值）
        stream.bit_rate = target_bitrate_kbps * 1_000
        stream.options = {"preset": preset} if settings["video"] in {"libx264", "libx265"} else {}
    elif settings["video"] in {"libx264", "libx265"}:
        # H.264 / H.265 使用 CRF 恒定质量
        stream.options = {"crf": str(crf), "preset": preset}
    elif settings["video"] == "libvpx-vp9":
        # VP9 需 b:v=0 才能真正进入 CRF 模式；ultrafast 对应实时 deadline
        stream.options = {"crf": str(crf), "b:v": "0", "deadline": "good" if preset != "ultrafast" else "realtime"}
    else:
        # MPEG-4 等编码器不支持 CRF，按 0–51 线性映射到 qscale（1–31）
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

        # 目标分辨率按最大宽度等比缩放并统一为偶数
        width, height = target_size(video_input.codec_context.width, video_input.codec_context.height, max_width)
        # 未指定 FPS 时沿用源帧率，两者都拿不到时退回 30 FPS
        rate = output_fps or video_input.average_rate or 30
        output_time_base = Fraction(1, 1) / Fraction(rate)
        # 记录上一帧 PTS，防止重编码后 PTS 回退导致写包失败
        previous_video_pts = -1
        # 仅处理首个音频流
        audio_input = next((stream for stream in source.streams if stream.type == "audio"), None)

        with av.open(output_path, mode="w", format=settings["container"]) as target:
            video_output = configure_video_stream(target, settings, width, height, rate, crf, preset, target_bitrate_kbps)
            audio_output = None
            audio_resampler = None
            if audio_input is not None:
                # 音频统一重采样为立体声 fltp，便于 AAC / Opus 编码
                sample_rate = audio_input.codec_context.sample_rate or 48_000
                audio_output = target.add_stream(settings["audio"], rate=sample_rate)
                audio_resampler = av.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=sample_rate)

            for packet in source.demux((video_input, audio_input) if audio_input else video_input):
                if packet.stream.type == "video":
                    for frame in packet.decode():
                        timestamp = frame.time
                        frame = frame.reformat(width=width, height=height, format="yuv420p")
                        if timestamp is not None:
                            # 按目标帧率重算时间戳；FPS 降低时丢弃被合并的重复帧
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

            # 冲刷视频编码器尾部缓存
            for encoded in video_output.encode():
                target.mux(encoded)
            if audio_output is not None:
                # 传入 None 冲刷重采样器，再冲刷音频编码器尾部缓存
                for resampled in audio_resampler.resample(None):
                    for encoded in audio_output.encode(resampled):
                        target.mux(encoded)
                for encoded in audio_output.encode():
                    target.mux(encoded)


# 注入全局样式：明亮 / 黑暗主题变量、队列表格网格布局与整页拖放遮罩
ui.add_head_html('''
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;500;700&display=swap');
:root { --ink:#1d1d1d; --paper:#ffffff; --card:#ffffff; --accent:var(--q-primary); --warning:#c10015; --line:#e0e0e0; --guide-a:#ffffff; --guide-b:#f5f5f5; --shadow:rgba(0,0,0,.12); }
body.body--dark { --ink:#f5f5f5; --paper:#121212; --card:#1d1d1d; --accent:var(--q-primary); --warning:#ff8a80; --line:#3a3a3a; --guide-a:#1d1d1d; --guide-b:#242424; --shadow:rgba(0,0,0,.45); }
body { background:var(--paper); color:var(--ink); font-family:'Noto Sans SC',sans-serif; transition:background .28s ease,color .28s ease; }
.hero-grid { background:none; }
.tool-card { border:1px solid var(--line); box-shadow:0 2px 10px var(--shadow); background:var(--card); transition:background .28s ease,border-color .28s ease,box-shadow .28s ease; }
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
.edit-config-panel { width:100%; border:1px solid #d1d5db; padding:.75rem; }
.edit-field-mode { width:150px; }
.edit-field-format { width:310px; }
.edit-field-preset { width:165px; }
.edit-field-row-parameter { width:390px; }
.edit-field-short { width:145px; }
.edit-field-bitrate { width:185px; }
.edit-field-output { width:220px; }
.edit-config-action { width:170px; height:56px; min-height:56px; align-self:end; }
.edit-config-action .q-btn__content { flex-wrap:nowrap; white-space:nowrap; }
</style>''', shared=True)


@ui.page("/")
def index() -> None:
    """主页面：文件输入、队列与单行编辑、输出位置与并行转换。"""
    # 队列数据：每个元素记录源路径、探测到的元信息与该行的目标参数
    selected_files: list[dict[str, object]] = []
    # 用字典持有输出目录，便于在回调中修改并共享同一个值
    output_directory = {"path": resolve_output_directory(None)}
    # 当前在单行编辑面板中被选中的队列表项
    selected_queue_item: dict[str, Optional[dict[str, object]]] = {"item": None}
    # 默认并行数取 CPU 核心数与 8 的较小值
    max_parallel_jobs = min(8, max(1, os.cpu_count() or 1))
    dark_mode = ui.dark_mode(value=False)

    with ui.column().classes("w-full min-h-screen items-center hero-grid px-5 py-10"):
        # 后台任务更新界面时的写入容器：NiceGUI 3.x 的 slot 栈按 asyncio 任务隔离，
        # 新建任务若不显式进入某个页面容器，ui.notify 等创建 UI 的操作无法确定所属客户端。
        background_update_slot = ui.column().classes("hidden")
        with ui.column().classes("w-full max-w-none gap-7"):
            with ui.row().classes("w-full items-start justify-between gap-4 flex-wrap"):
                with ui.column().classes("gap-1"):
                    ui.label("LOCAL VIDEO LAB").classes("mono text-sm font-bold")
                    ui.label("压缩一下").classes("text-4xl md:text-6xl font-bold leading-tight")

                def switch_dark_mode(event) -> None:
                    """按开关状态启用或关闭 NiceGUI 官方黑暗主题。"""
                    dark_mode.enable() if event.value else dark_mode.disable()

                ui.switch("黑暗模式", value=False, on_change=switch_dark_mode).props("dense color=primary").classes("official-theme-switch")
            ui.label("PyAV 驱动的离线视频工作台。文件仅在本机处理，成品可保存到指定目录。").classes("text-lg max-w-2xl")

            # 输入区与队列区放在同一张卡片：上方两个按钮负责添加文件，下方表格负责编辑参数
            with ui.card().classes("tool-card w-full p-5 md:p-8 rounded-none"):
                ui.label("01 / 输入视频与转换队列（可多选）").classes("mono font-bold")

                def render_queue() -> None:
                    """把内存中的队列数据刷新到表格与计数标签。"""
                    queue_label.set_text(f"待转换队列：{len(selected_files)} 个文件")
                    rows = []
                    for item in selected_files:
                        rows.append({
                            "id": str(item["path"]),
                            "file": f"{item['name']} ({item['size'] / 1024 / 1024:.1f} MB)",
                            "codec": item["codec"], "dimensions": item["dimensions"], "fps": item["fps"],
                            "duration": item["duration"], "bitrate": item["bitrate"],
                            # 每项参数都按该行自身保存的值显示，所见即转换时所用的值
                            "crf": item["target_crf"],
                            "width": item["target_width"] or "保持",
                            "target_fps": item["target_fps"] or "保持",
                            "target_bitrate": item["target_bitrate"] or "CRF",
                            "format": item["target_format"],
                            "output": normalise_output_mode(item["output_mode"]),
                            "status": item.get("status", "待转换"),
                        })
                    # 保留已选中行，避免每次刷新表格把编辑面板中的选中状态清空
                    queue_table.update_rows(rows, clear_selection=False)

                def remove_from_queue(item: dict[str, object]) -> None:
                    """从队列移除一项；上传产生的临时副本同步删除。"""
                    selected_files.remove(item)
                    if item.get("is_temporary"):
                        Path(item["path"]).unlink(missing_ok=True)
                    render_queue()

                def clear_queue() -> None:
                    """清空整条队列并清理全部上传临时文件。"""
                    for item in selected_files:
                        if item.get("is_temporary"):
                            Path(item["path"]).unlink(missing_ok=True)
                    selected_files.clear()
                    render_queue()

                async def add_local_paths(paths: list[Path]) -> None:
                    """读取本地原始路径的元数据并加入队列，不复制大文件。"""
                    # 已在队列中的同一路径直接跳过，避免重复添加
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
                            # 初始目标参数统一使用默认方案，之后可在编辑面板或批量按钮中调整
                            **default_target_settings(),
                            "output_mode": SOURCE_OUTPUT_MODE, "status": "待转换",
                            "is_temporary": False, "source_directory": path.parent,
                        })
                    render_queue()
                    if new_paths:
                        ui.notify(f"已加入 {len(new_paths) - len(failures)} 个本地视频文件" + (f"，跳过重复 {skipped} 个" if skipped else ""), type="positive")
                    if failures:
                        ui.notify("以下文件无法解析：\n" + "\n".join(failures), type="warning", close_button=True, timeout=0)

                async def select_local_files() -> None:
                    """弹原生多选文件窗口并把选择结果加入队列。"""
                    paths = await run.io_bound(choose_input_files, Path.home())
                    await add_local_paths(paths)

                async def select_local_directory() -> None:
                    """弹原生目录窗口并递归扫描其中的视频文件。"""
                    paths = await run.io_bound(choose_input_directory, Path.home())
                    if not paths:
                        ui.notify("所选文件夹中没有找到支持的视频文件。", type="warning")
                        return
                    await add_local_paths(paths)

                async def enrich_in_background_slot(item: dict[str, object]) -> None:
                    """在页面容器内执行异步探测任务，保证任务中可以安全更新界面。"""
                    with background_update_slot:
                        await enrich_uploaded_item(item)

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

                async def receive_upload(event) -> None:
                    """在上传请求中立即落盘文件副本，避免异步任务读取到已关闭的浏览器临时流。"""
                    file_path: Optional[Path] = None
                    try:
                        # NiceGUI 3.x 的 event.file 是 FileUpload：只有 name / content_type，
                        # 读取内容必须用异步的 save() 或 read()（2.x 的 .content 已移除）
                        upload_file = getattr(event, "file", None)
                        file_name = getattr(upload_file, "name", None)
                        if upload_file is None or not file_name:
                            raise ValueError("上传组件未提供文件。")
                        suffix = Path(str(file_name)).suffix or ".video"
                        file_path = WORK_DIR / f"{uuid.uuid4().hex}{suffix}"
                        # 大文件由 FileUpload 内部按流式写入临时目录，避免整块读入内存
                        await upload_file.save(file_path)
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
                        **default_target_settings(),
                        "output_mode": CUSTOM_OUTPUT_MODE, "status": "读取文件信息…",
                        "is_temporary": True, "source_directory": None,
                    }
                    selected_files.append(item)
                    render_queue()
                    ui.notify(f"已加入表格：{file_name}", type="positive")
                    background_tasks.create(enrich_in_background_slot(item), name=f"probe_{file_path.stem}")

                # 隐藏的上传组件：仅用其服务端接口，界面交互由下方的整页拖放逻辑驱动
                upload_component = ui.upload(on_upload=receive_upload, multiple=True, auto_upload=True, max_file_size=4 * 1024 ** 3, max_files=50).props('accept="video/*"').classes("hidden")
                upload_url = upload_component._props["url"]
                # 整页拖放提示遮罩
                ui.html('<div id="video-page-drop-overlay" class="page-drop-overlay"><div class="page-drop-overlay__panel">松手添加视频文件或文件夹<small id="video-page-drop-status">整页都支持拖放；Chrome / Edge 可递归遍历文件夹内所有常见视频文件。</small></div></div>')
                # 整页拖放脚本：递归读取拖入的文件/文件夹，并逐个 POST 到上传接口
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

                ui.separator().classes("my-5")
                # 队列标题行：文件计数与管理按钮都靠左排列
                with ui.row().classes("w-full items-center gap-3"):
                    queue_label = ui.label("待转换队列：0 个文件").classes("text-lg font-medium")
                    ui.button("清空待转换队列", icon="delete_sweep", on_click=clear_queue).props("flat dense")
                # 队列表格列定义：源信息与该行目标参数并列展示
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
                    {"name": "format", "label": "输出格式", "field": "format"},
                    {"name": "output", "label": "输出位置", "field": "output"},
                    {"name": "status", "label": "任务状态", "field": "status"},
                ]

                def clear_row_selection() -> None:
                    """取消表格选择并复位编辑面板。"""
                    selected_queue_item["item"] = None
                    selected_file_label.set_text("未选中文件：编辑面板显示的是默认方案，可直接保存到某一行或全部文件。")
                    reset_row_panel(None)
                    queue_table.selected.clear()
                    queue_table.update()

                def select_row_by_id(row_id: str) -> None:
                    """把指定行载入编辑面板，并同步 Quasar 的选中高亮。"""
                    item = next((candidate for candidate in selected_files if str(candidate["path"]) == row_id), None)
                    if item is None:
                        clear_row_selection()
                        return
                    selected_queue_item["item"] = item
                    selected_file_label.set_text(f"正在编辑：{item['name']}（宽度 / FPS / 码率为空时已自动回填源视频当前值）")
                    reset_row_panel(item)
                    queue_table.selected[:] = [row for row in queue_table.rows if row["id"] == row_id]
                    queue_table.update()

                def handle_row_click(event) -> None:
                    """点击行内任意单元格都能选中该行；再次点击同一行取消选择。"""
                    # js_handler 只透传被点击的那一行，这里同时兼容单值字典与列表两种形态
                    clicked_row = event.args
                    if isinstance(clicked_row, list):
                        clicked_row = next((row for row in clicked_row if isinstance(row, dict) and "id" in row), None)
                    if not isinstance(clicked_row, dict):
                        return
                    clicked_id = clicked_row["id"]
                    current_item = selected_queue_item["item"]
                    if current_item is not None and str(current_item["path"]) == clicked_id:
                        clear_row_selection()
                        return
                    select_row_by_id(clicked_id)

                def handle_selection_change(event) -> None:
                    """复选框改变选中时，把 Quasar 的选中结果同步到编辑面板。"""
                    selected_rows = getattr(event, "selection", None) or []
                    if not selected_rows:
                        clear_row_selection()
                        return
                    select_row_by_id(selected_rows[0]["id"])

                queue_table = ui.table(columns=table_columns, rows=[], row_key="id", selection="single", on_select=handle_selection_change, pagination={"rowsPerPage": 10}).classes("w-full my-3").props("dense flat bordered separator=cell")
                # 绑定行点击事件：整行任意单元格都可切换选中状态。
                # Quasar 的 row-click 第 1 个参数是 DOM 事件、第 2 个才是行数据，
                # 因此用 js_handler 只把行数据回传；点击复选框时直接跳过，交给 Quasar 自身的选择逻辑处理，
                # 避免“复选框”与“行点击”两个事件互相抵消。
                queue_table.on("row-click", handle_row_click,
                               js_handler="(evt, row) => { const target = evt && evt.target; "
                                          "if (target && target.closest && target.closest('.q-checkbox, [role=\"checkbox\"]')) return; "
                                          "emit(row); }")

                # 单行编辑面板：参数一行（可自动换行），操作按钮单独一行，避免超长横向滚动
                # 单行编辑面板：加 mt-2 与表格拉开距离，内部用 gap-y 分隔标签、参数行与按钮行
                with ui.element("div").classes("edit-config-panel mt-2 flex flex-col gap-y-2"):
                    selected_file_label = ui.label("未选中文件：编辑面板显示的是默认方案，可直接保存到某一行或全部文件。").classes("text-sm")

                    def apply_row_parameter_preset(event) -> None:
                        """选择“本行参数预设”后，把预设值填充到本行各输入框。"""
                        if event.value == PARAMETER_PRESET_CUSTOM:
                            return
                        preset_values = PARAMETER_PRESETS.get(event.value)
                        if not preset_values:
                            return
                        row_preset.set_value(preset_values["preset"])
                        row_crf.set_value(preset_values["crf"])
                        row_width.set_value(preset_values["width"])
                        row_fps.set_value(preset_values["fps"])
                        row_bitrate.set_value(preset_values["bitrate"])
                        ui.notify(f"已套用本行参数预设：{event.value}，点击“保存本行设置”或“全部文件使用此配置”后写入队列。", type="positive")

                    with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                        row_format = ui.select(list(FORMAT_SETTINGS), value=DEFAULT_TARGET_FORMAT, label="输出格式").classes("edit-field-format")
                        row_preset = ui.select(["ultrafast", "fast", "medium", "slow"], value=default_target_settings()["target_preset"], label="编码预设").classes("edit-field-preset")
                        row_parameter_preset = ui.select(
                            [PARAMETER_PRESET_CUSTOM, *PARAMETER_PRESETS],
                            value=DEFAULT_PARAMETER_PRESET,
                            label="本行参数预设",
                            on_change=apply_row_parameter_preset,
                        ).classes("edit-field-row-parameter")
                        row_crf = ui.select(CRF_CHOICES, value=default_target_settings()["target_crf"], label="目标 CRF", with_input=True, new_value_mode="add-unique").classes("edit-field-short")
                        row_width = ui.select(WIDTH_CHOICES, label="目标宽度", with_input=True, new_value_mode="add-unique", clearable=True).classes("edit-field-short")
                        row_fps = ui.select(FPS_CHOICES, label="目标 FPS", with_input=True, new_value_mode="add-unique", clearable=True).classes("edit-field-short")
                        row_bitrate = ui.select(BITRATE_CHOICES, label="目标码率 kbps", with_input=True, new_value_mode="add-unique", clearable=True).classes("edit-field-bitrate")

                        def sync_row_output_mode(event) -> None:
                            """输出位置下拉变化时立即写回当前选中行并重绘表格。"""
                            item = selected_queue_item["item"]
                            if item is None:
                                return
                            # 网页拖放/上传的文件拿不到真实源目录，自动回退为自定义输出目录
                            selected_output_mode = normalise_output_mode(event.value)
                            if selected_output_mode == SOURCE_OUTPUT_MODE and item.get("source_directory") is None:
                                selected_output_mode = CUSTOM_OUTPUT_MODE
                                row_output.set_value(CUSTOM_OUTPUT_MODE)
                                ui.notify("拖放/网页上传的文件无法读取原始目录，已自动改为“自定义输出目录”。如需保存到原文件夹，请使用“从本机选择文件”或“选择文件夹并扫描”。", type="warning", close_button=True)
                            item["output_mode"] = selected_output_mode
                            render_queue()

                        row_output = ui.select(OUTPUT_MODE_OPTIONS, value=CUSTOM_OUTPUT_MODE, label="输出位置", on_change=sync_row_output_mode).classes("edit-field-output")

                    def reset_row_panel(item: Optional[dict[str, object]]) -> None:
                        """把编辑面板复位为默认方案，或载入指定队列项的参数。"""
                        settings = dict(default_target_settings()) if item is None else item
                        values = {key: settings[key] for key in ("target_format", "target_preset", "target_crf", "target_width", "target_fps", "target_bitrate")}
                        if item is not None:
                            # 留空的宽度 / FPS / 码率用源视频实际值回填，方便直接沿用源参数
                            values["target_width"] = settings["target_width"] or settings.get("source_width")
                            values["target_fps"] = settings["target_fps"] or settings.get("source_fps")
                            values["target_bitrate"] = settings["target_bitrate"] or settings.get("source_bitrate_kbps")
                        row_format.set_value(values["target_format"])
                        row_preset.set_value(values["target_preset"])
                        row_crf.set_value(values["target_crf"])
                        row_width.set_value(values["target_width"])
                        row_fps.set_value(values["target_fps"])
                        row_bitrate.set_value(values["target_bitrate"])
                        row_output.set_value(normalise_output_mode(settings["output_mode"]) if item is not None else CUSTOM_OUTPUT_MODE)
                        row_parameter_preset.set_value(matching_parameter_preset(values))

                    def matching_parameter_preset(values: dict[str, object]) -> str:
                        """当面板参数与某组预设完全一致时高亮该预设，否则标记为自定义。"""
                        for preset_name, preset_values in PARAMETER_PRESETS.items():
                            if (values["target_crf"], values["target_width"], values["target_fps"], values["target_bitrate"], values["target_preset"]) == (
                                    preset_values["crf"], preset_values["width"], preset_values["fps"], preset_values["bitrate"], preset_values["preset"]):
                                return preset_name
                        return PARAMETER_PRESET_CUSTOM

                    def current_panel_settings() -> dict[str, object]:
                        """读取编辑面板中的当前参数（不含输出位置，便于逐行回退）。"""
                        return {
                            "target_format": row_format.value,
                            "target_preset": row_preset.value,
                            "target_crf": row_crf.value,
                            "target_width": row_width.value,
                            "target_fps": row_fps.value,
                            "target_bitrate": row_bitrate.value,
                        }

                    def resolve_output_mode(item: dict[str, object]) -> tuple[str, bool]:
                        """按当前选择决定该行的输出位置；缺少源目录时回退并告知调用方。"""
                        selected_output_mode = normalise_output_mode(row_output.value)
                        if selected_output_mode == SOURCE_OUTPUT_MODE and item.get("source_directory") is None:
                            return CUSTOM_OUTPUT_MODE, True
                        return selected_output_mode, False

                    def apply_row_settings() -> None:
                        """把编辑面板中的参数写入当前选中的队列项。"""
                        item = selected_queue_item["item"]
                        if item is None:
                            ui.notify("请先点击表格中的文件行。", type="warning")
                            return
                        output_mode, fell_back = resolve_output_mode(item)
                        if fell_back:
                            row_output.set_value(CUSTOM_OUTPUT_MODE)
                            ui.notify("拖放/网页上传的文件无法读取原始目录，已自动改为“自定义输出目录”。如需保存到原文件夹，请使用“从本机选择文件”或“选择文件夹并扫描”。", type="warning", close_button=True)
                        item.update(**current_panel_settings(), output_mode=output_mode)
                        render_queue()
                        ui.notify(f"已保存本行设置：{item['name']}", type="positive")

                    def apply_settings_to_all_files() -> None:
                        """把编辑面板中的当前参数一次性套用到队列中的所有文件。"""
                        if not selected_files:
                            ui.notify("队列里还没有文件。", type="warning")
                            return
                        settings = current_panel_settings()
                        fallback = 0
                        for item in selected_files:
                            output_mode, fell_back = resolve_output_mode(item)
                            fallback += 1 if fell_back else 0
                            item.update(**settings, output_mode=output_mode)
                        render_queue()
                        message = f"已将当前配置套用到 {len(selected_files)} 个文件。"
                        if fallback:
                            message += f"其中 {fallback} 个拖放/上传文件无法定位原文件夹，保留为自定义输出目录。"
                            ui.notify(message, type="warning", close_button=True)
                        else:
                            ui.notify(message, type="positive")

                    def remove_selected_row() -> None:
                        """移除当前在编辑面板中选中的那一行。"""
                        item = selected_queue_item["item"]
                        if item is None:
                            ui.notify("请先点击表格中的文件行。", type="warning")
                            return
                        selected_queue_item["item"] = None
                        remove_from_queue(item)
                        selected_file_label.set_text("已移除文件。")

                    # 操作按钮单独一行放在参数下方，并留出与上方控件/表格之间的间距
                    with ui.row().classes("w-full items-center gap-3 flex-wrap mt-6"):
                        ui.button("保存本行设置", icon="save", on_click=apply_row_settings).props("outline")
                        ui.button("全部文件使用此配置", icon="format_paint", on_click=apply_settings_to_all_files).props("color=primary")
                        ui.button("移除选中行", icon="delete", on_click=remove_selected_row).props("outline color=negative")
                render_queue()

            with ui.card().classes("tool-card w-full p-5 md:p-8 rounded-none"):
                ui.label("02 / 输出位置与批量转换").classes("mono font-bold")
                ui.label("用上面的“全部文件使用此配置”把同一套参数一次性写入整队；输出位置也可在这里批量覆盖。").classes("text-sm text-gray-600")

                with ui.row().classes("w-full items-center gap-3 mt-4"):
                    # 限制最大宽度，避免超长路径把按钮推到行尾
                    output_dir_label = ui.label(f"自定义输出目录：{output_directory['path']}").classes("text-sm break-all max-w-[65%]")

                    async def select_output_directory() -> None:
                        """通过 Windows 原生窗口重新选择自定义输出目录。"""
                        selected = await run.io_bound(choose_output_directory, output_directory["path"])
                        if selected is not None:
                            output_directory["path"] = selected
                            output_dir_label.set_text(f"自定义输出目录：{selected}")

                    ui.button("选择自定义输出目录", icon="folder_open", on_click=select_output_directory).props("outline")

                def set_all_output_mode(mode: str) -> None:
                    """批量覆盖整条队列的输出位置；无法定位原文件夹的行会回退为自定义目录。"""
                    if not selected_files:
                        ui.notify("队列里还没有文件。", type="warning")
                        return
                    changed = 0
                    fallback = 0
                    # 逐行设置，统计因缺少源目录而回退的数量
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
                ui.label("单行下拉会立即生效；批量按钮可一次性覆盖队列输出位置。拖放上传文件因浏览器限制不能获取真实原文件夹。").classes("mono text-xs mt-6")
                ui.label("提示：默认套用【原始】原画质保留（CRF 18 / slow，保持源宽度与帧率）；CRF 越小画质越高、文件通常越大。").classes("mono text-xs mt-6")
                status = ui.label("等待任务").classes("mono text-sm mt-2")

                async def start_conversion() -> None:
                    """校验参数、生成任务列表，并用多进程池并行完成全部转换。"""
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
                        # 预先算好每个文件的输出路径与解析后的参数，参数非法的直接记入失败
                        jobs: list[tuple] = []
                        for index, input_file in enumerate(list(selected_files), start=1):
                            try:
                                # 每个文件都用它自己保存的那份参数（由编辑面板或“全部文件使用此配置”写入）
                                file_format = input_file["target_format"]
                                file_preset = input_file["target_preset"]
                                file_crf = parse_crf(input_file["target_crf"])
                                file_width = parse_optional_positive_int(input_file["target_width"], "目标宽度")
                                file_fps = parse_optional_positive_int(input_file["target_fps"], "目标帧率")
                                file_bitrate = parse_optional_positive_int(input_file["target_bitrate"], "目标码率")
                                output_extension = FORMAT_SETTINGS[file_format]["extension"]
                                file_output_dir, final_output_mode, fell_back_to_custom = choose_effective_output_directory(
                                    input_file.get("output_mode"), output_dir, input_file.get("source_directory"),
                                )
                                input_file["output_mode"] = final_output_mode
                                if fell_back_to_custom:
                                    input_file["status"] = "已改用自定义输出目录"
                                output_path = build_available_output_path(file_output_dir, str(input_file["name"]), output_extension)
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
                        # 信号量限制同时在跑的任务数，实现受控并发
                        semaphore = asyncio.Semaphore(parallelism)
                        loop = asyncio.get_running_loop()

                        async def process_job(job: tuple) -> None:
                            """执行单个文件的转换，并实时更新该行任务状态。"""
                            (index, input_file, output_path, file_format, file_crf,
                             file_preset, file_width, file_fps, file_bitrate) = job
                            async with semaphore:
                                input_file["status"] = "转换中"
                                status.set_text(f"正在转换 {index}/{len(selected_files)}：{input_file['name']}（并行 {parallelism}）")
                                render_queue()
                                try:
                                    # 转码放到独立进程执行，避免长时间阻塞主事件循环
                                    await loop.run_in_executor(
                                        executor, convert_video, str(input_file["path"]), str(output_path), file_format,
                                        file_crf, file_preset, file_width, file_fps, file_bitrate,
                                    )
                                except Exception as exc:
                                    # 转换失败时删除可能已生成的残缺文件
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

                # 并行数随启动按钮一行放置
                with ui.row().classes("w-full items-end gap-4 mt-4 flex-wrap"):
                    parallelism_select = ui.select(
                        {count: f"{count} 个并行进程" for count in range(1, max_parallel_jobs + 1)},
                        value=max_parallel_jobs,
                        label="批量并行数",
                    ).classes("w-56")
                    button = ui.button("开始压缩 / 转换", icon="bolt", on_click=start_conversion).props("color=primary")


if __name__ in {"__main__", "__mp_main__"}:
    # 桌面本地模式启动：绑定回环地址，用原生窗口打开界面，关闭窗口后自动停止服务
    ui.run(title="Local Video Lab",
           host="127.0.0.1",
           port=9297,
           reload=False,
           language="zh-CN",
           native=True,
           # 原生窗口的初始大小（宽, 高），仅在 native=True 时生效；队列表格较宽，建议不小于 1280
           window_size=(1200, 750),
           # fullscreen=True 可直接全屏启动，frameless=True 去掉标题栏
           )
