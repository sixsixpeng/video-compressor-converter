"""本地视频压缩与格式转换工具（wxPython + PyAV）。"""
from __future__ import annotations  # 延迟解析类型注解，避免注解中的类型在定义时就必须可求值。

import threading
from fractions import Fraction  # 用有理数表示帧时间基，避免浮点运算带来的时间戳误差。
from pathlib import Path
from typing import Optional

import av
import wx

VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv",
}

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

DEFAULT_FORMAT = "MP4 (H.264 + AAC，通用兼容)"
PARAMETER_PRESET_CUSTOM = "自定义 / 不套用预设"
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


def normalise_even(value: int) -> int:
    """将尺寸调整为适用于 yuv420p 的偶数，且不小于 2。"""
    return max(2, value - value % 2)


def parse_optional_positive_int(value: str, label: str) -> Optional[int]:
    """解析可留空的正整数参数，并为非法输入提供可读错误。"""
    if not value.strip():
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{label}必须是正整数。") from exc
    if result <= 0:
        raise ValueError(f"{label}必须大于 0。")
    return result


def target_size(source_width: int, source_height: int, max_width: Optional[int]) -> tuple[int, int]:
    """按最大宽度等比例缩放；未指定或大于源宽度时保留源尺寸。"""
    if not max_width or max_width >= source_width:
        return normalise_even(source_width), normalise_even(source_height)
    height = round(source_height * max_width / source_width)
    return normalise_even(max_width), normalise_even(height)


def build_available_output_path(output_dir: Path, input_path: Path, extension: str) -> Path:
    """为同名输出追加序号，避免覆盖已有文件。"""
    candidate = output_dir / f"{input_path.stem}_converted.{extension}"
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"{input_path.stem}_converted_{suffix}.{extension}"
        suffix += 1
    return candidate


def format_duration(seconds: Optional[float]) -> str:
    """将媒体时长格式化为小时、分钟和秒。"""
    if seconds is None or seconds < 0:
        return "—"
    total_seconds = round(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def format_bitrate(bits_per_second: Optional[int]) -> str:
    """将比特率格式化为 kbps 或 Mbps。"""
    if not bits_per_second or bits_per_second <= 0:
        return "—"
    if bits_per_second >= 1_000_000:
        return f"{bits_per_second / 1_000_000:.2f} Mbps"
    return f"{bits_per_second / 1_000:.0f} kbps"


def probe_video(path: Path) -> dict[str, object]:
    """仅读取首个视频流及首个音频流的媒体参数，不解码完整文件。"""
    with av.open(str(path)) as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("未找到视频流。")
        context = stream.codec_context
        audio_stream = next((item for item in container.streams if item.type == "audio"), None)
        fps = stream.average_rate
        duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else None
        if duration is None and container.duration is not None:
            duration = float(container.duration / av.time_base)
        bitrate = container.bit_rate or getattr(context, "bit_rate", None)
        audio_context = audio_stream.codec_context if audio_stream is not None else None
        audio_layout = audio_context.layout if audio_context is not None else None
        return {
            "codec": str(context.name or "未知").upper(),
            "dimensions": f"{context.width} × {context.height}",
            "fps": f"{float(fps):.3g}" if fps else "—",
            "duration": format_duration(duration),
            "bitrate": format_bitrate(bitrate),
            "source_width": context.width,
            "source_height": context.height,
            "audio_codec": str(audio_context.name or "未知").upper() if audio_context is not None else None,
            "audio_sample_rate": audio_context.sample_rate if audio_context is not None else None,
            "audio_channels": len(audio_layout.channels) if audio_layout is not None else None,
        }


def convert_video(input_path: Path, output_path: Path, output_label: str, crf: int,
                  preset: str, max_width: Optional[int], output_fps: Optional[int],
                  target_bitrate_kbps: Optional[int]) -> None:
    """使用 PyAV 将视频和首个音频流转码到指定格式。"""
    settings = FORMAT_SETTINGS[output_label]
    with av.open(str(input_path)) as source:
        video_input = next((stream for stream in source.streams if stream.type == "video"), None)
        if video_input is None:
            raise ValueError("未在此文件中找到视频流。")

        source_width = video_input.codec_context.width
        source_height = video_input.codec_context.height
        width, height = target_size(source_width, source_height, max_width)
        rate = output_fps or video_input.average_rate or 30
        output_time_base = Fraction(1, 1) / Fraction(rate)
        previous_video_pts = -1
        # 只处理首个音频流，与视频一起转码；无音频的视频仍可正常输出。
        audio_input = next((stream for stream in source.streams if stream.type == "audio"), None)

        with av.open(str(output_path), mode="w", format=settings["container"]) as target:
            video_output = target.add_stream(settings["video"], rate=rate)
            video_output.width, video_output.height = width, height
            video_output.pix_fmt = "yuv420p"
            if target_bitrate_kbps:
                video_output.bit_rate = target_bitrate_kbps * 1_000
                video_output.options = {"preset": preset} if settings["video"] in {"libx264", "libx265"} else {}
            elif settings["video"] in {"libx264", "libx265"}:
                video_output.options = {"crf": str(crf), "preset": preset}
            elif settings["video"] == "libvpx-vp9":
                video_output.options = {
                    "crf": str(crf),
                    "b:v": "0",
                    "deadline": "good" if preset != "ultrafast" else "realtime",
                }
            else:
                video_output.options = {"qscale": str(max(1, min(31, round(crf * 31 / 51))))}

            audio_output = None
            audio_resampler = None
            if audio_input is not None:
                sample_rate = audio_input.codec_context.sample_rate or 48_000
                audio_output = target.add_stream(settings["audio"], rate=sample_rate)
                # 将不同来源的音频统一到编码器可处理的采样格式和声道布局。
                audio_resampler = av.audio.resampler.AudioResampler(
                    format="fltp", layout="stereo", rate=sample_rate,
                )

            streams = (video_input, audio_input) if audio_input is not None else video_input
            for packet in source.demux(streams):
                if packet.stream.type == "video":
                    for frame in packet.decode():
                        timestamp = frame.time
                        frame = frame.reformat(width=width, height=height, format="yuv420p")
                        if timestamp is not None:
                            # 将源时间戳换算到输出帧时间基；丢弃重复或倒退时间戳以避免输出时序异常。
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


class VideoFileDropTarget(wx.FileDropTarget):
    """将拖入窗口的文件路径交给主窗口加入队列。"""

    def __init__(self, add_paths) -> None:
        """保存处理拖放文件的回调。"""
        super().__init__()
        self.add_paths = add_paths

    def OnDropFiles(self, x: int, y: int, filenames: list[str]) -> bool:
        """接收拖放的文件和目录，并报告是否已处理。"""
        self.add_paths([Path(name) for name in filenames])
        return True


class ConverterFrame(wx.Frame):
    """提供视频选择、转码参数设置和批量转换的桌面窗口。"""

    def __init__(self) -> None:
        """创建窗口控件并初始化转换队列。"""
        super().__init__(None, title="Local Video Lab - 视频压缩与转换")
        self.items: list[dict[str, object]] = []
        self.pending_probe_items: set[int] = set()
        self.user_column_widths: dict[int, int] = {}
        self.hovered_row = -1
        self.busy = False
        self.output_directory = Path.home() / "Downloads"
        self._build_ui()
        self.SetMinSize(self.FromDIP((780, 620)))
        display_area = wx.Display(self).GetClientArea()
        self.SetSize(int(display_area.width * 0.82), int(display_area.height * 0.82))
        self.Centre()
        wx.CallAfter(self._resize_layout)

    def _build_ui(self) -> None:
        """创建文件队列、转换选项和状态区域。"""
        panel = wx.Panel(self)
        self.main_panel = panel
        outer = wx.BoxSizer(wx.VERTICAL)
        row_gap = self.FromDIP(8)

        file_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.add_files_button = wx.Button(panel, label="添加视频文件")
        self.add_folder_button = wx.Button(panel, label="扫描文件夹")
        self.clear_button = wx.Button(panel, label="清空队列")
        file_buttons.Add(self.add_files_button, 0, wx.RIGHT, 8)
        file_buttons.Add(self.add_folder_button, 0, wx.RIGHT, 8)
        file_buttons.Add(self.clear_button, 0)
        self.status_label = wx.StaticText(
            panel, label="就绪。", style=wx.ALIGN_RIGHT | wx.ST_ELLIPSIZE_END,
        )
        file_buttons.AddStretchSpacer(1)
        file_buttons.Add(self.status_label, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(file_buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, row_gap)

        self.file_list = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES | wx.BORDER_SUNKEN,
        )
        self.table_columns = [
            "文件名 / 大小", "编码", "源尺寸", "源 FPS", "时长", "源码率",
            "目标 CRF", "目标宽度", "目标 FPS", "目标码率 kbps", "编码预设",
            "配置模式", "输出格式", "输出位置", "任务状态",
        ]
        self.file_column_min_widths = [0] * len(self.table_columns)
        self.file_column_max_widths = [
            500, 150, 140, 100, 100, 140, 100, 120, 100, 140, 130, 150, 260, 200, 420,
        ]
        self.table_column_index = {name: index for index, name in enumerate(self.table_columns)}
        for index, label in enumerate(self.table_columns):
            self.file_list.InsertColumn(index, label)
        self.file_list.SetMinSize((-1, self.FromDIP(120)))
        outer.Add(self.file_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, row_gap)
        self.file_list.SetDropTarget(VideoFileDropTarget(self.add_paths))
        self.file_list.Bind(wx.EVT_MOTION, self.on_table_motion)
        self.file_list.Bind(wx.EVT_LIST_COL_END_DRAG, self.on_file_column_end_drag)

        self.settings_panel = panel
        self.settings_fields: list[wx.BoxSizer] = []
        self.settings_labels: list[wx.StaticText] = []
        self.settings_controls: list[wx.Window] = []
        self.settings_wrap_sizer = wx.WrapSizer(wx.HORIZONTAL)

        def add_setting(label: str, control: wx.Window) -> None:
            """将标签和控件并排加入可自动换行的表单容器。"""
            label_control = wx.StaticText(self.settings_panel, label=label)
            field = wx.BoxSizer(wx.HORIZONTAL)
            field.Add(label_control, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(6))
            field.Add(control, 1, wx.EXPAND)
            field.SetMinSize((self.FromDIP(260), -1))
            self.settings_fields.append(field)
            self.settings_labels.append(label_control)
            self.settings_controls.append(control)
            self.settings_wrap_sizer.Add(
                field, 0, wx.EXPAND | wx.RIGHT | wx.BOTTOM, row_gap,
            )

        self.parameter_preset_choice = wx.Choice(
            self.settings_panel, choices=[PARAMETER_PRESET_CUSTOM, *PARAMETER_PRESETS],
        )
        self.parameter_preset_choice.SetSelection(0)
        self.parameter_preset_choice.SetToolTip(
            "选择预设会填入 CRF、编码预设、最大宽度、FPS 和目标码率；留空项会恢复为保持源宽度/帧率或使用 CRF 模式。之后仍可手动调整参数。"
        )
        add_setting("参数预设组", self.parameter_preset_choice)

        self.format_choice = wx.Choice(self.settings_panel, choices=list(FORMAT_SETTINGS))
        self.format_choice.SetSelection(list(FORMAT_SETTINGS).index(DEFAULT_FORMAT))
        self.format_choice.SetToolTip(
            "选择输出封装和视频/音频编码。H.264 兼容性较好；H.265/HEVC、VP9 通常可在相近画质下得到更小文件，但编码更慢且设备兼容性可能较低。"
        )
        add_setting("输出格式", self.format_choice)

        self.preset_choice = wx.Choice(self.settings_panel, choices=["ultrafast", "fast", "medium", "slow"])
        self.preset_choice.SetSelection(2)
        self.preset_choice.SetToolTip(
            "控制编码速度与压缩效率：ultrafast 最快但同画质下文件通常更大；slow 编码更久，通常能得到更小文件或更好的固定码率画质。"
        )
        add_setting("编码预设", self.preset_choice)

        self.crf_input = wx.SpinCtrl(self.settings_panel, min=0, max=51, initial=23)
        self.crf_input.SetToolTip(
            "恒定质量参数：CRF 越小画质越高、文件通常越大；数值越大压缩越强、文件通常越小。建议从 23 左右开始调整。填写目标码率后 CRF 不生效；不同编码器的实际效果会有差异。"
        )
        add_setting("CRF (0-51)", self.crf_input)

        self.width_input = wx.TextCtrl(self.settings_panel)
        self.width_input.SetToolTip(
            "限制输出视频的最大宽度并保持原宽高比；不会放大低分辨率源视频。留空时保持源尺寸。降低宽度会减少像素数量，文件通常也会变小。"
        )
        add_setting("最大宽度", self.width_input)

        self.fps_input = wx.TextCtrl(self.settings_panel)
        self.fps_input.SetToolTip(
            "设置输出帧率。留空保持原帧率。降低帧率会减少画面帧数和文件大小，但运动可能不够流畅；高于源帧率不会增加真实细节，且可能增大文件。"
        )
        add_setting("输出 FPS", self.fps_input)

        self.bitrate_input = wx.TextCtrl(self.settings_panel)
        self.bitrate_input.SetToolTip(
            "设置目标视频码率（kbps）；数值越高文件通常越大、画质更有保障，数值过低会产生明显压缩失真。估算视频大小约为码率 × 时长 ÷ 8，另加音频和封装开销。填写后使用码率模式，CRF 不生效。"
        )
        add_setting("目标码率 kbps", self.bitrate_input)

        self.output_mode_choice = wx.Choice(self.settings_panel, choices=["统一输出目录", "源文件所在目录"])
        self.output_mode_choice.SetSelection(0)
        self.output_mode_choice.SetToolTip(
            "统一输出目录会保存到下方选择的目录；源文件所在目录会将成品保存到每个视频的原文件夹。此选项不影响画质或文件大小，同名成品会自动追加序号以避免覆盖。"
        )
        add_setting("输出位置", self.output_mode_choice)

        self.output_path = wx.TextCtrl(self.settings_panel, value=str(self.output_directory), style=wx.TE_READONLY)
        self.output_path.SetToolTip("当前自定义输出目录；仅在“统一输出目录”模式下使用。")
        self.browse_button = wx.Button(self.settings_panel, label="自定义输出目录")
        self.browse_button.SetToolTip("选择转换成品的统一保存目录；使用“源文件所在目录”模式时会忽略此路径。")

        outer.Add(self.settings_wrap_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, row_gap)
        output_row = wx.BoxSizer(wx.HORIZONTAL)
        output_row.Add(self.output_path, 1, wx.EXPAND | wx.RIGHT, 8)
        output_row.Add(self.browse_button, 0)
        outer.Add(output_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, row_gap)

        self.progress = wx.Gauge(panel, range=100)
        self.progress.SetMinSize((self.FromDIP(180), -1))
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        bottom.Add(self.progress, 1, wx.EXPAND | wx.RIGHT, 12)
        self.start_button = wx.Button(panel, label="开始转换")
        self.start_button.SetDefault()
        bottom.Add(self.start_button, 0)
        outer.Add(bottom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, row_gap)

        panel.SetSizer(outer)
        self._resize_file_columns()
        self.add_files_button.Bind(wx.EVT_BUTTON, self.on_add_files)
        self.add_folder_button.Bind(wx.EVT_BUTTON, self.on_add_folder)
        self.clear_button.Bind(wx.EVT_BUTTON, self.on_clear_queue)
        self.browse_button.Bind(wx.EVT_BUTTON, self.on_browse_output)
        self.parameter_preset_choice.Bind(wx.EVT_CHOICE, self.on_parameter_preset_changed)
        self.format_choice.Bind(wx.EVT_CHOICE, self.on_table_settings_changed)
        self.preset_choice.Bind(wx.EVT_CHOICE, self.on_table_settings_changed)
        self.output_mode_choice.Bind(wx.EVT_CHOICE, self.on_table_settings_changed)
        self.crf_input.Bind(wx.EVT_SPINCTRL, self.on_table_settings_changed)
        self.crf_input.Bind(wx.EVT_TEXT, self.on_table_settings_changed)
        self.width_input.Bind(wx.EVT_TEXT, self.on_table_settings_changed)
        self.fps_input.Bind(wx.EVT_TEXT, self.on_table_settings_changed)
        self.bitrate_input.Bind(wx.EVT_TEXT, self.on_table_settings_changed)
        self.start_button.Bind(wx.EVT_BUTTON, self.on_start_conversion)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_SIZE, self.on_resize)

    def on_resize(self, event: wx.SizeEvent) -> None:
        """在窗口尺寸事件完成后重新排列列表和设置字段。"""
        event.Skip()
        wx.CallAfter(self._resize_layout)

    def _resize_layout(self) -> None:
        """按当前窗口尺寸排列自动换行的设置字段。"""
        self.main_panel.Layout()
        self._resize_status_label()
        self._resize_settings_fields()
        self.main_panel.Layout()

    def _resize_status_label(self) -> None:
        """将顶部状态提示限制在按钮右侧的可用宽度内。"""
        if not hasattr(self, "status_label"):
            return
        buttons_right = max(
            control.GetPosition().x + control.GetSize().width
            for control in (self.add_files_button, self.add_folder_button, self.clear_button)
        )
        available_width = max(
            1, self.main_panel.GetClientSize().width - buttons_right - self.FromDIP(8),
        )
        self.status_label.SetMaxSize((available_width, -1))

    def _set_status_text(self, text: str) -> None:
        """更新顶部状态提示，并保留完整文字供悬停查看。"""
        self.status_label.SetLabel(text)
        self.status_label.SetToolTip(text)
        self._resize_status_label()
        self.main_panel.Layout()

    def _resize_file_columns(self, columns: Optional[list[int]] = None) -> None:
        """按表头和单元格内容计算指定列宽，并限制在各列的最小与最大宽度内。"""
        if not hasattr(self, "file_list"):
            return
        dc = wx.ClientDC(self.file_list)
        dc.SetFont(self.file_list.GetFont())
        padding = self.FromDIP(16)
        column_indices = range(len(self.table_columns)) if columns is None else columns
        for index in column_indices:
            header_width = dc.GetTextExtent(self.table_columns[index])[0]
            min_width = header_width + padding
            self.file_column_min_widths[index] = min_width
            max_width = self.FromDIP(self.file_column_max_widths[index])
            if index in self.user_column_widths:
                width = self.user_column_widths[index]
            else:
                width = header_width
                for row in range(self.file_list.GetItemCount()):
                    content = self.file_list.GetItemText(row, index)
                    width = max(width, dc.GetTextExtent(content)[0])
                width += padding
            width = max(min_width, min(max_width, width))
            self.file_list.SetColumnWidth(index, width)

    def on_file_column_end_drag(self, event: wx.ListEvent) -> None:
        """保存用户拖动后的列宽，并限制在列宽范围内。"""
        column = event.GetColumn()
        if column < 0 or column >= len(self.file_column_min_widths):
            event.Skip()
            return
        min_width = self.file_column_min_widths[column]
        max_width = self.FromDIP(self.file_column_max_widths[column])
        width = max(min_width, min(max_width, self.file_list.GetColumnWidth(column)))
        self.user_column_widths[column] = width
        self.file_list.SetColumnWidth(column, width)
        event.Skip()

    def _resize_settings_fields(self) -> None:
        """按当前宽度调整字段尺寸，避免自动换行时控件高度被压缩。"""
        if not hasattr(self, "settings_panel"):
            return
        available_width = self.settings_wrap_sizer.GetSize().width
        if available_width <= 0:
            return

        minimum_width = self.FromDIP(260)
        minimum_control_width = self.FromDIP(100)
        gap = self.FromDIP(10)
        # 保持标签与控件并排所需的最小宽度，空间不足时自动减少每行字段数。
        columns = max(1, min(4, (available_width + gap) // (minimum_width + gap)))
        field_width = max(minimum_width, (available_width - gap * columns) // columns)
        label_control_gap = self.FromDIP(6)
        for field, label, control in zip(
            self.settings_fields, self.settings_labels, self.settings_controls,
        ):
            field.SetMinSize((field_width, -1))
            label_width = label.GetBestSize().width
            control_width = max(
                minimum_control_width, field_width - label_width - label_control_gap,
            )
            control_height = max(control.GetBestSize().height, self.FromDIP(28))
            control.SetMinSize((control_width, control_height))
        self.settings_wrap_sizer.Layout()

    def add_paths(self, paths: list[Path]) -> None:
        """将有效视频路径加入队列，忽略重复项并扫描拖入的目录。"""
        existing = {Path(item["path"]).resolve() for item in self.items}
        candidates: list[Path] = []
        for path in paths:
            if path.is_dir():
                candidates.extend(
                    candidate for candidate in path.rglob("*")
                    if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTENSIONS
                )
            elif path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                candidates.append(path)

        added = 0
        probe_items: list[dict[str, object]] = []
        for path in sorted((candidate.resolve() for candidate in candidates), key=lambda item: str(item).lower()):
            if path in existing:
                continue
            existing.add(path)
            item: dict[str, object] = {
                "path": path,
                "name": path.name,
                "size": path.stat().st_size,
                "codec": "读取中…",
                "dimensions": "—",
                "fps": "—",
                "duration": "—",
                "bitrate": "—",
                "source_width": None,
                "source_height": None,
                "audio_codec": None,
                "audio_sample_rate": None,
                "audio_channels": None,
                "target_crf": self.crf_input.GetValue(),
                "target_width": self.width_input.GetValue(),
                "target_fps": self.fps_input.GetValue(),
                "target_bitrate": self.bitrate_input.GetValue(),
                "target_preset": self.preset_choice.GetStringSelection(),
                "target_format": self.format_choice.GetStringSelection(),
                "config_mode": "共用配置",
                "output_mode": self.output_mode_choice.GetStringSelection(),
                "status": "读取视频参数中…",
                "output_path": None,
                "output_size": None,
                "output_metadata": None,
                "output_probe_error": None,
            }
            row = self.file_list.InsertItem(self.file_list.GetItemCount(), "")
            item["row"] = row
            self.items.append(item)
            self._render_item_row(item)
            self.pending_probe_items.add(id(item))
            probe_items.append(item)
            added += 1

        if added:
            self._resize_file_columns()
        self._set_status_text(f"已加入 {added} 个视频文件；队列共 {len(self.items)} 个。")
        if probe_items:
            thread = threading.Thread(target=self._probe_items, args=(probe_items,), daemon=True)
            thread.start()

    def _render_item_row(self, item: dict[str, object]) -> None:
        """将源视频信息和当前目标参数写入对应表格行。"""
        row = int(item["row"])
        target_width = item["target_width"] or "保持"
        target_fps = item["target_fps"] or "保持"
        target_bitrate = item["target_bitrate"] or "CRF"
        values = [
            f"{item['name']} ({item['size'] / 1024 / 1024:.1f} MB)",
            item["codec"], item["dimensions"], item["fps"], item["duration"], item["bitrate"],
            item["target_crf"], target_width, target_fps, target_bitrate, item["target_preset"],
            item["config_mode"], item["target_format"], item["output_mode"], item["status"],
        ]
        for column, value in enumerate(values):
            self.file_list.SetItem(row, column, str(value))
        if self.hovered_row == row:
            self.file_list.SetToolTip(self._build_row_tooltip(item))

    def on_table_motion(self, event: wx.MouseEvent) -> None:
        """鼠标进入不同视频行时更新该行的完整转换信息提示。"""
        row, _ = self.file_list.HitTest(event.GetPosition())
        if row != self.hovered_row:
            self.hovered_row = row
            item = next((candidate for candidate in self.items if candidate["row"] == row), None)
            self.file_list.SetToolTip(self._build_row_tooltip(item) if item is not None else "")
        event.Skip()

    def _build_row_tooltip(self, item: dict[str, object]) -> str:
        """汇总源文件参数、目标设置及已生成成品的实际参数。"""
        source_audio = "无音频流"
        if item["audio_codec"]:
            source_audio = (
                f"{item['audio_codec']}，{item['audio_sample_rate'] or '未知'} Hz，"
                f"{item['audio_channels'] or '未知'} 声道"
            )

        source_width = item["source_width"]
        source_height = item["source_height"]
        requested_width = str(item["target_width"] or "").strip()
        try:
            max_width = parse_optional_positive_int(requested_width, "最大宽度")
            if max_width is not None and max_width < 2:
                raise ValueError("最大宽度至少为 2。")
            if source_width and source_height:
                target_dimensions = " × ".join(
                    str(value) for value in target_size(source_width, source_height, max_width)
                )
            else:
                target_dimensions = "读取源尺寸后确定"
        except ValueError:
            target_dimensions = f"参数无效：{requested_width}"

        target_fps = str(item["target_fps"] or "").strip() or f"保持源帧率（{item['fps']} FPS）"
        target_bitrate = str(item["target_bitrate"] or "").strip()
        quality = f"视频码率 {target_bitrate} kbps" if target_bitrate else f"CRF {item['target_crf']}"
        format_label = str(item["target_format"])
        format_settings = FORMAT_SETTINGS[format_label]
        source_path = Path(item["path"])
        output_directory = source_path.parent if item["output_mode"] == "源文件所在目录" else self.output_directory
        output_path = item["output_path"]

        lines = [
            "转换前（源文件）",
            f"文件：{source_path}",
            f"大小：{item['size'] / 1024 / 1024:.1f} MB",
            f"视频：{item['codec']}，{item['dimensions']}，{item['fps']} FPS，时长 {item['duration']}，码率 {item['bitrate']}",
            f"音频：{source_audio}",
            "",
            "转换设置 / 目标",
            f"输出格式：{format_label}",
            f"目标编码：视频 {format_settings['video']}，音频 {format_settings['audio']}",
            f"目标尺寸：{target_dimensions}；目标 FPS：{target_fps}",
            f"质量参数：{quality}；编码预设：{item['target_preset']}",
            f"输出位置：{item['output_mode']}（{output_directory}）",
            f"任务状态：{item['status']}",
        ]
        if output_path is not None and item["output_metadata"] is not None:
            output_metadata = item["output_metadata"]
            output_audio = "无音频流"
            if output_metadata["audio_codec"]:
                output_audio = (
                    f"{output_metadata['audio_codec']}，{output_metadata['audio_sample_rate'] or '未知'} Hz，"
                    f"{output_metadata['audio_channels'] or '未知'} 声道"
                )
            lines.extend([
                "",
                "转换后（实际成品）",
                f"文件：{output_path}",
                f"大小：{item['output_size'] / 1024 / 1024:.1f} MB",
                f"视频：{output_metadata['codec']}，{output_metadata['dimensions']}，{output_metadata['fps']} FPS，时长 {output_metadata['duration']}，码率 {output_metadata['bitrate']}",
                f"音频：{output_audio}",
            ])
        elif item["output_probe_error"]:
            lines.extend([
                "",
                "转换后（实际成品）",
                f"文件：{output_path}",
                f"大小：{item['output_size'] / 1024 / 1024:.1f} MB",
                f"成品参数读取失败：{item['output_probe_error']}",
            ])
        elif output_path is not None:
            lines.extend(["", f"预期成品路径：{output_path}"])
        return "\n".join(lines)

    def _refresh_queue_parameters(self) -> None:
        """将当前全局配置同步到队列所有视频的目标参数列。"""
        for item in self.items:
            item.update(
                target_crf=self.crf_input.GetValue(),
                target_width=self.width_input.GetValue(),
                target_fps=self.fps_input.GetValue(),
                target_bitrate=self.bitrate_input.GetValue(),
                target_preset=self.preset_choice.GetStringSelection(),
                target_format=self.format_choice.GetStringSelection(),
                output_mode=self.output_mode_choice.GetStringSelection(),
            )
            self._render_item_row(item)
        self._resize_file_columns(list(range(6, 14)))

    def _probe_items(self, items: list[dict[str, object]]) -> None:
        """在后台读取新增视频的媒体参数，并把结果交回界面线程。"""
        for item in items:
            try:
                metadata = probe_video(Path(item["path"]))
            except Exception as exc:
                wx.CallAfter(self._finish_probe, item, None, str(exc))
            else:
                wx.CallAfter(self._finish_probe, item, metadata, None)

    def _finish_probe(self, item: dict[str, object], metadata: Optional[dict[str, object]],
                      error: Optional[str]) -> None:
        """更新视频源参数和解析状态，忽略已从队列移除的项目。"""
        self.pending_probe_items.discard(id(item))
        if not any(queued_item is item for queued_item in self.items):
            return
        if metadata is None:
            item.update(codec="无法读取", status=f"解析失败：{error}")
        else:
            item.update(**metadata, status="待转换")
        self._render_item_row(item)
        self._resize_file_columns([1, 2, 3, 4, 5, 14])

    def on_table_settings_changed(self, event: wx.CommandEvent) -> None:
        """配置变化时同步更新队列中的目标参数列。"""
        self._refresh_queue_parameters()
        event.Skip()

    def on_add_files(self, event: wx.CommandEvent) -> None:
        """打开多选文件对话框并添加视频。"""
        with wx.FileDialog(
            self,
            "选择视频文件",
            defaultDir=str(Path.home()),
            wildcard="视频文件|*.3gp;*.avi;*.flv;*.m2ts;*.m4v;*.mkv;*.mov;*.mp4;*.mpeg;*.mpg;*.mts;*.ts;*.webm;*.wmv|所有文件|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.add_paths([Path(name) for name in dialog.GetPaths()])

    def on_add_folder(self, event: wx.CommandEvent) -> None:
        """选择目录并递归添加其中支持的视频文件。"""
        with wx.DirDialog(self, "选择视频文件夹", defaultPath=str(Path.home())) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.add_paths([Path(dialog.GetPath())])

    def on_clear_queue(self, event: wx.CommandEvent) -> None:
        """清空当前待转换队列。"""
        if self.busy:
            return
        self.items.clear()
        self.hovered_row = -1
        self.file_list.SetToolTip("")
        self.file_list.DeleteAllItems()
        self._resize_file_columns()
        self._set_status_text("队列已清空。")
        self.progress.SetValue(0)

    def on_browse_output(self, event: wx.CommandEvent) -> None:
        """选择统一输出目录，并更新窗口显示的路径。"""
        with wx.DirDialog(
            self, "选择视频输出目录", defaultPath=str(self.output_directory),
            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,
        ) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.output_directory = Path(dialog.GetPath()).resolve()
                self.output_path.SetValue(str(self.output_directory))

    def on_parameter_preset_changed(self, event: wx.CommandEvent) -> None:
        """将选中的参数预设写入编码、尺寸、帧率和码率控件。"""
        preset = PARAMETER_PRESETS.get(self.parameter_preset_choice.GetStringSelection())
        if preset is None:
            return

        # 留空项表示保留源宽度/帧率，或继续使用 CRF 而非固定码率。
        self.crf_input.SetValue(preset["crf"])
        self.preset_choice.SetStringSelection(preset["preset"])
        self.width_input.SetValue("" if preset["width"] is None else str(preset["width"]))
        self.fps_input.SetValue("" if preset["fps"] is None else str(preset["fps"]))
        self.bitrate_input.SetValue("" if preset["bitrate"] is None else str(preset["bitrate"]))
        self._refresh_queue_parameters()

    def _read_settings(self) -> tuple[str, int, str, Optional[int], Optional[int], Optional[int]]:
        """读取并验证窗口中的全局转码参数。"""
        format_label = self.format_choice.GetStringSelection()
        if not format_label:
            raise ValueError("请选择输出格式。")
        preset = self.preset_choice.GetStringSelection()
        crf = self.crf_input.GetValue()
        width = parse_optional_positive_int(self.width_input.GetValue(), "最大宽度")
        fps = parse_optional_positive_int(self.fps_input.GetValue(), "输出 FPS")
        bitrate = parse_optional_positive_int(self.bitrate_input.GetValue(), "目标码率")
        if width is not None and width < 2:
            raise ValueError("最大宽度至少为 2。")
        return format_label, crf, preset, width, fps, bitrate

    def on_start_conversion(self, event: wx.CommandEvent) -> None:
        """校验设置后在后台线程启动队列转换。"""
        if self.busy:
            return
        if not self.items:
            wx.MessageBox("请先添加至少一个视频文件。", "没有待转换文件", wx.OK | wx.ICON_INFORMATION)
            return
        if any(id(item) in self.pending_probe_items for item in self.items):
            wx.MessageBox("正在读取队列中的视频参数，请稍后再开始转换。", "视频信息读取中", wx.OK | wx.ICON_INFORMATION)
            return
        try:
            settings = self._read_settings()
            output_directory = self.output_directory.resolve()
            if self.output_mode_choice.GetSelection() == 0:
                output_directory.mkdir(parents=True, exist_ok=True)
                if not output_directory.is_dir():
                    raise ValueError(f"输出目录无效：{output_directory}")
        except (OSError, ValueError) as exc:
            wx.MessageBox(str(exc), "转换参数无效", wx.OK | wx.ICON_ERROR)
            return

        self.busy = True
        self.progress.SetValue(0)
        self._set_controls_enabled(False)
        jobs = [dict(item) for item in self.items]
        # 转码放在工作线程，避免大文件处理阻塞窗口响应。
        thread = threading.Thread(
            target=self._run_batch,
            args=(jobs, settings, output_directory, self.output_mode_choice.GetSelection() == 1),
            daemon=True,
        )
        thread.start()

    def _run_batch(self, jobs: list[dict[str, object]], settings: tuple,
                   output_directory: Path, use_source_directory: bool) -> None:
        """在工作线程中顺序转换文件，并将结果安全回传给界面线程。"""
        format_label, crf, preset, width, fps, bitrate = settings
        successes = 0
        failures = 0
        for number, item in enumerate(jobs, start=1):
            input_path = Path(item["path"])
            output_dir = input_path.parent if use_source_directory else output_directory
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                extension = FORMAT_SETTINGS[format_label]["extension"]
                output_path = build_available_output_path(output_dir, input_path, extension)
                # wx 控件只能在主线程更新，后台任务通过 CallAfter 投递界面更新。
                wx.CallAfter(self._set_expected_output, int(item["row"]), output_path)
                wx.CallAfter(self._set_row_status, int(item["row"]), f"转换中 ({number}/{len(jobs)})")
                wx.CallAfter(self._set_status_text, f"正在转换 {number}/{len(jobs)}：{input_path.name}")
                try:
                    convert_video(input_path, output_path, format_label, crf, preset, width, fps, bitrate)
                except Exception:
                    # 转码失败时清除不完整成品，源文件保持不变。
                    output_path.unlink(missing_ok=True)
                    raise
            except Exception as exc:
                failures += 1
                wx.CallAfter(self._set_row_status, int(item["row"]), f"失败：{exc}")
            else:
                successes += 1
                output_size = output_path.stat().st_size
                try:
                    output_metadata = probe_video(output_path)
                except Exception as exc:
                    wx.CallAfter(
                        self._set_output_result, int(item["row"]), output_path,
                        output_size, None, str(exc),
                    )
                else:
                    wx.CallAfter(
                        self._set_output_result, int(item["row"]), output_path,
                        output_size, output_metadata, None,
                    )
            wx.CallAfter(self.progress.SetValue, round(number * 100 / len(jobs)))

        summary = f"批量处理完成：成功 {successes} 个，失败 {failures} 个。"
        wx.CallAfter(self._finish_batch, summary)

    def _set_row_status(self, row: int, status: str) -> None:
        """更新列表中指定视频的处理状态。"""
        if row < self.file_list.GetItemCount():
            for item in self.items:
                if item["row"] == row:
                    item["status"] = status
                    self._render_item_row(item)
                    self._resize_file_columns([14])
                    break

    def _set_expected_output(self, row: int, output_path: Path) -> None:
        """记录当前任务预期的成品路径，供表格行提示展示。"""
        item = next((candidate for candidate in self.items if candidate["row"] == row), None)
        if item is not None:
            item["output_path"] = output_path
            self._render_item_row(item)

    def _set_output_result(self, row: int, output_path: Path, output_size: int,
                           metadata: Optional[dict[str, object]], probe_error: Optional[str]) -> None:
        """保存转码成品的实际参数或成品参数读取错误。"""
        item = next((candidate for candidate in self.items if candidate["row"] == row), None)
        if item is None:
            return
        item.update(
            output_path=output_path,
            output_size=output_size,
            output_metadata=metadata,
            output_probe_error=probe_error,
            status=f"完成：{output_path.name}",
        )
        self._render_item_row(item)
        self._resize_file_columns([14])

    def _set_controls_enabled(self, enabled: bool) -> None:
        """在转换期间禁用可修改队列或参数的控件。"""
        for control in (
            self.add_files_button, self.add_folder_button, self.clear_button,
            self.format_choice, self.parameter_preset_choice, self.preset_choice,
            self.crf_input, self.width_input,
            self.fps_input, self.bitrate_input, self.output_mode_choice,
            self.browse_button, self.start_button,
        ):
            control.Enable(enabled)

    def _finish_batch(self, summary: str) -> None:
        """恢复界面并显示批量转换结果。"""
        self.busy = False
        self._set_controls_enabled(True)
        self._set_status_text(summary)
        if "失败 0 个" not in summary:
            wx.MessageBox(summary, "转换完成", wx.OK | wx.ICON_WARNING)

    def on_close(self, event: wx.CloseEvent) -> None:
        """避免窗口关闭后后台转换继续运行且无法查看结果。"""
        if self.busy or self.pending_probe_items:
            wx.MessageBox("视频参数读取或转换仍在进行中，请等待任务完成后再关闭。", "任务进行中", wx.OK | wx.ICON_INFORMATION)
            event.Veto()
            return
        event.Skip()


def main() -> None:
    """启动 wxPython 桌面应用。"""
    application = wx.App(False)
    frame = ConverterFrame()
    frame.Show()
    application.MainLoop()


if __name__ == "__main__":
    main()
