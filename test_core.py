"""无需外部媒体样本的核心参数与转码自检。"""
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import av
from app import (CUSTOM_OUTPUT_MODE, PARAMETER_PRESETS, SOURCE_OUTPUT_MODE,
                 build_available_output_path, build_output_name,
                 choose_effective_output_directory, convert_video, normalise_even,
                 normalise_output_mode, parse_crf, parse_optional_positive_int,
                 probe_video, resolve_output_directory, scan_video_directory)
def create_sample(path: Path) -> None:
    with av.open(path, "w", format="matroska") as container:
        stream = container.add_stream("mpeg4", rate=24)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        audio_stream = container.add_stream("pcm_s16le", rate=48_000)
        for number in range(12):
            frame = av.VideoFrame(64, 48, "rgb24")
            frame.planes[0].update(bytes([number * 20, 40, 140]) * (64 * 48))
            for packet in stream.encode(frame):
                container.mux(packet)
        for number in range(12):
            audio_frame = av.AudioFrame(format="s16", layout="stereo", samples=1024)
            audio_frame.sample_rate = 48_000
            audio_frame.pts = number * 1024
            audio_frame.planes[0].update(bytes(1024 * 2 * 2))
            for packet in audio_stream.encode(audio_frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        for packet in audio_stream.encode():
            container.mux(packet)
def main() -> None:
    assert normalise_even(1920) == 1920
    assert normalise_even(1921) == 1920
    assert normalise_even(1) == 2
    assert parse_optional_positive_int(None, "帧率") is None
    assert parse_optional_positive_int("", "帧率") is None
    assert parse_optional_positive_int("30", "帧率") == 30
    assert parse_crf("0") == 0
    assert parse_crf(51) == 51
    assert "【1080P】通用推荐（1080P / 30 FPS / CRF 23）" in PARAMETER_PRESETS
    assert "【720P】通用分享（720P / 30 FPS / CRF 28）" in PARAMETER_PRESETS
    assert "【1080P】固定码率（60 FPS / 12000 kbps）" in PARAMETER_PRESETS
    preset_names = list(PARAMETER_PRESETS)
    assert preset_names.index("【4K】高质量归档（2160P / 60 FPS / CRF 18）") < preset_names.index("【2K】高质量归档（1440P / 60 FPS / CRF 20）")
    assert preset_names.index("【1080P】通用推荐（1080P / 30 FPS / CRF 23）") < preset_names.index("【720P】通用分享（720P / 30 FPS / CRF 28）")
    assert preset_names.index("【1080P】省空间（1080P / 24 FPS / CRF 30）") < preset_names.index("【1080P】固定码率（30 FPS / 6000 kbps）")
    assert preset_names.index("【1080P】固定码率（60 FPS / 12000 kbps）") < preset_names.index("【900P】窗口录屏（1600 宽 / 30 FPS / CRF 24）")
    for preset in PARAMETER_PRESETS.values():
        assert parse_crf(preset["crf"]) == preset["crf"]
        assert parse_optional_positive_int(preset["width"], "目标宽度") == preset["width"]
        assert parse_optional_positive_int(preset["fps"], "目标帧率") == preset["fps"]
        assert parse_optional_positive_int(preset["bitrate"], "目标码率") == preset["bitrate"]
        assert preset["preset"] in {"ultrafast", "fast", "medium", "slow"}
    assert build_output_name("旅行.原片.MOV", "mp4") == "旅行.原片_converted.mp4"
    with tempfile.TemporaryDirectory() as directory:
        output_dir = resolve_output_directory(directory)
        source_dir = Path(directory) / "source"
        source_dir.mkdir()
        assert normalise_output_mode("统一目录") == CUSTOM_OUTPUT_MODE
        assert normalise_output_mode("源文件所在目录") == SOURCE_OUTPUT_MODE
        assert choose_effective_output_directory(CUSTOM_OUTPUT_MODE, output_dir, source_dir) == (output_dir, CUSTOM_OUTPUT_MODE, False)
        assert choose_effective_output_directory(SOURCE_OUTPUT_MODE, output_dir, source_dir) == (source_dir.resolve(), SOURCE_OUTPUT_MODE, False)
        assert choose_effective_output_directory(SOURCE_OUTPUT_MODE, output_dir, None) == (output_dir, CUSTOM_OUTPUT_MODE, True)
        first_output = build_available_output_path(output_dir, "旅行.原片.MOV", "mp4")
        assert first_output.name == "旅行.原片_converted.mp4"
        first_output.touch()
        assert build_available_output_path(output_dir, "旅行.原片.MOV", "mp4").name == "旅行.原片_converted_1.mp4"
        source = Path(directory) / "sample.mkv"
        nested_directory = Path(directory) / "nested"
        nested_directory.mkdir()
        additional_video = nested_directory / "clip.MP4"
        additional_video.touch()
        (nested_directory / "notes.txt").touch()
        output = Path(directory) / "sample.mp4"
        bitrate_output = Path(directory) / "sample_bitrate.mp4"
        webm_output = Path(directory) / "sample_individual.webm"
        hevc_output = Path(directory) / "sample_hevc.mp4"
        ts_output = Path(directory) / "sample.ts"
        parallel_outputs = [Path(directory) / "parallel_1.mp4", Path(directory) / "parallel_2.mp4"]
        create_sample(source)
        scanned = scan_video_directory(Path(directory))
        assert source.resolve() in scanned
        assert additional_video.resolve() in scanned
        assert all(path.suffix.lower() != ".txt" for path in scanned)
        metadata = probe_video(source)
        assert metadata["dimensions"] == "64 × 48"
        assert metadata["fps"] == "24"
        assert metadata["codec"] == "MPEG4"
        assert metadata["source_width"] == 64
        assert metadata["source_height"] == 48
        assert metadata["source_fps"] == 24
        convert_video(str(source), str(output), "MP4 (H.264 + AAC，通用兼容)", 28, "fast", 32, 12)
        convert_video(str(source), str(bitrate_output), "MP4 (H.264 + AAC，通用兼容)", 23, "fast", None, None, 500)
        convert_video(str(source), str(webm_output), "WebM (VP9 + Opus)", 33, "medium", None, None)
        convert_video(str(source), str(hevc_output), "MP4 (H.265 / HEVC + AAC，更小体积)", 28, "ultrafast", None, None)
        convert_video(str(source), str(ts_output), "MPEG-TS (H.264 + AAC，电视 / 流媒体)", 23, "ultrafast", None, None)
        assert bitrate_output.stat().st_size > 0
        assert webm_output.stat().st_size > 0
        assert hevc_output.stat().st_size > 0
        assert ts_output.stat().st_size > 0
        with ProcessPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(convert_video, str(source), str(path), "MP4 (H.264 + AAC，通用兼容)", 26, "ultrafast", 32, 12)
                for path in parallel_outputs
            ]
            for future in futures:
                future.result(timeout=30)
        assert all(path.stat().st_size > 0 for path in parallel_outputs)
        with av.open(output) as converted:
            video = next(stream for stream in converted.streams if stream.type == "video")
            assert (video.codec_context.width, video.codec_context.height) == (32, 24)
            assert video.codec_context.name == "h264"
            assert any(stream.type == "audio" for stream in converted.streams)
    print("核心参数与视频转码自检通过")
if __name__ == "__main__":
    main()

