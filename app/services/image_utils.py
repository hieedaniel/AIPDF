"""图片处理：上传流读取、格式嗅探、EXIF 纠正、缩放、转码、切页。

设计要点：
1. 不信任客户端给出的 content_type / 文件名，通过文件头（magic bytes）判断真实格式。
2. 分块读取上传流，边读边校验大小，避免恶意大文件打爆内存。
3. 统一转成 RGB 后再编码为 JPEG（透明通道用白底合成），PDF 体积更小、兼容性最好。
4. 全程一张一张处理，内存占用与图片张数无关。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import io
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile

from app.config import Settings
from app.config import settings as default_settings
from app.core import exceptions as err

logger = logging.getLogger(__name__)

# PyMuPDF / Pillow 对超大图片有默认像素上限，这里显式设定一个明确值
with contextlib.suppress(Exception):
    Image.MAX_IMAGE_PIXELS = 268_435_456  # 256M 像素，配合单文件体积限制足够安全

# 文件头 → 格式名
_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"BM", "BMP"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
)

SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF"}

# 一页的图片数据：(JPEG 字节, 像素宽, 像素高)
PageImage = Tuple[bytes, int, int]


def sniff_format(data: bytes) -> Optional[str]:
    """通过文件头识别图片格式，识别不出返回 None。"""
    for magic, fmt in _MAGIC:
        if data.startswith(magic):
            return fmt
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


async def read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """分块读取上传文件，超过 max_bytes 立即中断并清理。"""
    name = upload.filename or "unnamed"
    buf = io.BytesIO()
    total = 0
    try:
        while True:
            chunk = await upload.read(1024 * 1024)  # 1MB
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise err.file_too_large(name, max_bytes / 1024 / 1024)
            buf.write(chunk)
    finally:
        await upload.close()

    if total == 0:
        raise err.empty_file(name)
    return buf.getvalue()


def decode_image(
    data: bytes,
    name: str,
    *,
    cfg: Settings = default_settings,
) -> Image.Image:
    """字节流 → 已纠正方向、已缩放、RGB 模式的 Pillow Image。"""
    fmt = sniff_format(data)
    if fmt is None or fmt not in SUPPORTED_FORMATS:
        raise err.unsupported_type(name, fmt)

    try:
        with Image.open(io.BytesIO(data)) as raw:
            raw.load()
            # 手机拍摄的照片普遍带 EXIF 方向信息，必须先纠正，否则会躺倒
            img = ImageOps.exif_transpose(raw) or raw
            img = _to_rgb(img)
    except UnidentifiedImageError as exc:
        raise err.decode_failed(name, "无法识别的图片数据") from exc
    except Image.DecompressionBombError as exc:  # type: ignore[attr-defined]
        raise err.decode_failed(name, "图片像素过大") from exc
    except OSError as exc:
        raise err.decode_failed(name, str(exc)) from exc
    except MemoryError as exc:
        raise err.decode_failed(name, "内存不足") from exc

    min_side = min(img.size)
    if min_side < cfg.min_image_side:
        raise err.image_too_small(name, cfg.min_image_side)

    img = downscale(img, cfg.max_image_side)
    logger.info(
        "图片解析成功 name=%s format=%s size=%dx%d mode=%s",
        name, fmt, img.width, img.height, img.mode,
    )
    return img


def _to_rgb(img: Image.Image) -> Image.Image:
    """统一转 RGB；带透明通道的用白底合成，避免出现黑底。"""
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if has_alpha:
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")


def downscale(img: Image.Image, max_side: int) -> Image.Image:
    """长边超过 max_side 时等比缩小（LANCZOS），否则原样返回。"""
    longest = max(img.size)
    if max_side <= 0 or longest <= max_side:
        return img
    ratio = max_side / float(longest)
    new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
    logger.info("图片过大，缩放 %dx%d -> %dx%d", img.width, img.height, *new_size)
    return img.resize(new_size, Image.Resampling.LANCZOS)


def encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=max(1, min(quality, 95)), optimize=True)
    return buf.getvalue()


def split_pages(
    img: Image.Image,
    page_mode: str,
    *,
    usable_ratio: float,
    quality: int,
) -> List[PageImage]:
    """把一张图片切成若干"页"。

    - fit   ：不切分，整张图等比缩放进一页 A4。
    - split ：按"可打印区域宽高比"垂直切片，长截图自然分成多页 A4。

    usable_ratio = 可打印区域宽 / 高（已扣除页边距）。
    """
    if page_mode != "split":
        return [(encode_jpeg(img, quality), img.width, img.height)]

    slice_height = int(round(img.width / usable_ratio))
    if slice_height <= 0 or slice_height >= img.height:
        # 图片本身没超过一页，退化成 fit
        return [(encode_jpeg(img, quality), img.width, img.height)]

    pages: List[PageImage] = []
    for top in range(0, img.height, slice_height):
        bottom = min(top + slice_height, img.height)
        if bottom - top < 8:  # 忽略切出来的极窄残条
            break
        # copy() 之后才能安全释放原图引用
        tile = img.crop((0, top, img.width, bottom))
        pages.append((encode_jpeg(tile, quality), tile.width, tile.height))
    if not pages:  # 理论上不会发生
        return [(encode_jpeg(img, quality), img.width, img.height)]
    logger.info("长图切分为 %d 页（原图 %dx%d）", len(pages), img.width, img.height)
    return pages


def guess_extension(filename: Optional[str]) -> str:
    """(仅用于日志/错误提示) 取原始文件后缀。"""
    return Path(filename or "").suffix.lower().lstrip(".") or "unknown"


# data:image/jpeg;base64,... 前缀
_DATA_URL_RE = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def decode_base64_payload(raw: str, name: str, max_bytes: int) -> bytes:
    """解码 base64 图片（兼容 data URL 前缀与换行），并做大小限制。"""
    if not raw or not raw.strip():
        raise err.empty_file(name)

    payload = _WHITESPACE_RE.sub("", _DATA_URL_RE.sub("", raw.strip()))
    # 先用字符串长度粗筛，避免对超大串做无意义的解码
    if len(payload) * 3 // 4 > max_bytes:
        raise err.file_too_large(name, max_bytes / 1024 / 1024)

    try:
        data = base64.b64decode(payload)
    except (binascii.Error, ValueError) as exc:
        raise err.decode_failed(name, "base64 解码失败") from exc

    if not data:
        raise err.empty_file(name)
    if len(data) > max_bytes:
        raise err.file_too_large(name, max_bytes / 1024 / 1024)
    return data
