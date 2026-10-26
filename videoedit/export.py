"""최종 출력 규격: 사용자가 첨부한 1편 완성본과 동일한 .mov (HEVC hvc1, BT.709, 30fps, ~6.9Mbps, AAC 44.1kHz 스테레오)."""
import pathlib
from .common import load_rules, ff, duration


def export_like_ref(src, dst=None):
    r = load_rules()["export"]
    src = pathlib.Path(src)
    dst = pathlib.Path(dst) if dst else src.with_suffix(".mov")
    xp = (f"bframes={r['bframes']}:keyint={r['keyint']}:min-keyint={r['keyint']}:scenecut=0:"
          f"colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited:log-level=error")
    ff(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx265", "-profile:v", r["profile"], "-level:v", r["level"], "-tag:v", r["tag"], "-pix_fmt", r["pix_fmt"],
        "-b:v", f"{r['video_kbps']}k", "-x265-params", xp, "-r", str(r["fps"]),
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-ar", str(r["audio_hz"]), "-ac", str(r["audio_ch"]), "-b:a", f"{r['audio_kbps']}k",
        "-t", f"{duration(str(src)):.3f}", "-f", "mov", str(dst)])
    return dst
