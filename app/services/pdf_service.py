"""核心业务编排：多张图片 → 一个 A4 PDF。

分两个阶段，避免阻塞事件循环：
1. 异步阶段：以流式方式读取上传内容并做大小限制（IO 密集）。
2. 线程池阶段：图片解码 / 缩放 / 编码 / 合成 PDF（CPU 密集，会释放 GIL 的部分也交给线程）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

import anyio
from starlette.datastructures import UploadFile

from app.config import Settings
from app.config import settings as default_settings
from app.core import exceptions as err
from app.services import image_utils, pdf_builder, storage

logger = logging.getLogger(__name__)

PAGE_MODES = ("fit", "split")


@dataclass
class ConvertResult:
    """一次成功转换的结果。"""

    path: Path
    page_count: int
    size_bytes: int
    source_count: int
    expires_at: Optional[datetime] = None

    @property
    def file_name(self) -> str:
        return self.path.name


async def convert_uploads_to_pdf(
    uploads: Sequence[UploadFile],
    *,
    page_mode: Optional[str] = None,
    pdf_title: Optional[str] = None,
    cfg: Settings = default_settings,
) -> ConvertResult:
    """接收原始上传对象，完成校验 → 读取 → 合成 PDF → 落盘。"""
    files = [f for f in uploads if f is not None and (f.filename or "").strip()]
    if not files:
        raise err.no_files()
    if len(files) > cfg.max_file_count:
        raise err.too_many_files(cfg.max_file_count)

    total_bytes = 0
    raw_files: List[Tuple[str, bytes]] = []
    for index, upload in enumerate(files, start=1):
        name = upload.filename or f"file-{index}"
        raw = await image_utils.read_upload_limited(upload, cfg.max_file_size_bytes)
        total_bytes += len(raw)
        if total_bytes > cfg.max_total_size_bytes:
            raise err.total_too_large(cfg.max_total_size_mb)
        raw_files.append((name, raw))

    # CPU 密集部分丢到线程池，保持事件循环可响应
    return await anyio.to_thread.run_sync(
        lambda: convert_streams_to_pdf(
            raw_files,
            page_mode=page_mode,
            pdf_title=pdf_title,
            cfg=cfg,
            source_count=len(raw_files),
        )
    )


async def convert_base64_to_pdf(
    images: Sequence[Tuple[str, str]],
    *,
    page_mode: Optional[str] = None,
    pdf_title: Optional[str] = None,
    cfg: Settings = default_settings,
) -> ConvertResult:
    """base64 入口的异步包装（multipart 版本的 JSON 版）。"""
    if not images:
        raise err.no_files()
    if len(images) > cfg.max_file_count:
        raise err.too_many_files(cfg.max_file_count)

    return await anyio.to_thread.run_sync(
        lambda: _decode_base64_and_build(images, page_mode=page_mode, pdf_title=pdf_title, cfg=cfg)
    )


def _decode_base64_and_build(
    images: Sequence[Tuple[str, str]],
    *,
    page_mode: Optional[str],
    pdf_title: Optional[str],
    cfg: Settings,
) -> ConvertResult:
    raw_files: List[Tuple[str, bytes]] = []
    total_bytes = 0
    for name, encoded in images:
        data = image_utils.decode_base64_payload(encoded, name, cfg.max_file_size_bytes)
        total_bytes += len(data)
        if total_bytes > cfg.max_total_size_bytes:
            raise err.total_too_large(cfg.max_total_size_mb)
        raw_files.append((name, data))

    return convert_streams_to_pdf(
        raw_files, page_mode=page_mode, pdf_title=pdf_title, cfg=cfg, source_count=len(images)
    )


async def save_temp_image(upload: UploadFile, *, cfg: Settings = default_settings) -> Tuple[str, Path]:
    """分张上传流程第一步：校验并规范化原图，暂存到 var/uploads/。

    返回 (image_id, 暂存路径)。规范化（EXIF 纠正 + 转 RGB/JPEG）在此完成，
    后续合并无需重复解码，也避免了一堆格式兼容问题。
    """
    name = (upload.filename or "unnamed").strip() or "unnamed"
    data = await image_utils.read_upload_limited(upload, cfg.max_file_size_bytes)
    return await anyio.to_thread.run_sync(lambda: _save_temp_image_sync(data, name, cfg))


def _save_temp_image_sync(data: bytes, name: str, cfg: Settings) -> Tuple[str, Path]:
    image = image_utils.decode_image(data, name, cfg=cfg)
    try:
        # 暂存环节质量拉高一点，减少后续二次压缩的损失
        payload = image_utils.encode_jpeg(image, max(cfg.jpeg_quality, 92))
    finally:
        image.close()

    path = storage.new_upload_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)  # 运行期目录被清掉也能自愈
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, path)  # 原子落盘，避免读到半张图
    logger.info("暂存原图 id=%s name=%s bytes=%d", path.stem, name, len(payload))
    return path.stem, path


async def convert_temp_images_to_pdf(
    image_ids: Sequence[str],
    *,
    rotations: Optional[Sequence[int]] = None,
    page_mode: Optional[str] = None,
    pdf_title: Optional[str] = None,
    cfg: Settings = default_settings,
) -> ConvertResult:
    """分张上传流程第二步：按传入的 image_id 顺序合并为一个 PDF。

    rotations 与 image_ids 一一对应，顺时针角度（0/90/180/270），
    在服务端旋转是无损的（不需要在手机上重新编码一遍）。
    """
    if not image_ids:
        raise err.no_files()
    if len(image_ids) > cfg.max_file_count:
        raise err.too_many_files(cfg.max_file_count)

    return await anyio.to_thread.run_sync(
        lambda: _merge_temp_images(
            image_ids,
            rotations=rotations,
            page_mode=page_mode,
            pdf_title=pdf_title,
            cfg=cfg,
        )
    )


def _merge_temp_images(
    image_ids: Sequence[str],
    *,
    rotations: Optional[Sequence[int]] = None,
    page_mode: Optional[str],
    pdf_title: Optional[str],
    cfg: Settings,
) -> ConvertResult:
    angles = list(rotations or [])
    if angles and len(angles) != len(image_ids):
        raise err.invalid_param(
            f"rotations 数量（{len(angles)}）必须与 image_ids（{len(image_ids)}）一致"
        )

    # 先只 stat 不读取：几十张图的总量校验不需要把内容放进内存
    total_bytes = 0
    for image_id in image_ids:
        path = storage.resolve_upload_path(cfg, image_id)
        if not path.is_file():
            raise err.image_not_found(image_id)
        total_bytes += storage.path_size(path)
        if total_bytes > cfg.max_total_size_bytes:
            raise err.total_too_large(cfg.max_total_size_mb)

    # 惰性读取：真正生成时一次只载入一张原图
    def lazy_files() -> Iterator[Tuple[str, bytes]]:
        for image_id in image_ids:
            path = storage.resolve_upload_path(cfg, image_id)
            yield path.name, path.read_bytes()

    try:
        result = convert_streams_to_pdf(
            lazy_files(),
            rotations=angles or None,
            page_mode=page_mode,
            pdf_title=pdf_title,
            cfg=cfg,
            source_count=len(image_ids),
        )
    except Exception:
        # 失败时保留暂存图，方便客户端直接重试（TTL 后由后台任务兜底清理）
        logger.warning("合并失败，保留 %d 张暂存原图供重试", len(image_ids))
        raise

    # 成功即清理，不多占磁盘
    for image_id in image_ids:
        storage.remove_upload(cfg, image_id)
    return result


def convert_streams_to_pdf(
    raw_files: Iterable[Tuple[str, bytes]],
    *,
    page_mode: Optional[str] = None,
    pdf_title: Optional[str] = None,
    cfg: Settings = default_settings,
    source_count: Optional[int] = None,
    rotations: Optional[Sequence[int]] = None,
) -> ConvertResult:
    """同步版本：入参为 [(原始文件名, 图片字节), ...]，按顺序合成 PDF。

    raw_files 可以是生成器。页面是惰性生成的：取一页、写一页、释放一页，
    因此合成几十张图时内存占用基本恒定（不再与张数线性增长）。
    """
    mode = (page_mode or cfg.page_mode or "fit").strip().lower()
    if mode not in PAGE_MODES:
        raise err.invalid_param(f"page_mode 只能是 {PAGE_MODES} 之一，收到：{page_mode!r}")

    total = source_count if source_count is not None else _safe_len(raw_files)
    if total is not None and total <= 0:
        raise err.no_files()

    angles = list(rotations or []) if rotations else []
    if angles and total is not None and len(angles) != total:
        raise err.invalid_param(
            f"rotations 数量（{len(angles)}）必须与图片数量（{total}）一致"
        )

    items = _iter_raw_items(raw_files, angles)
    # 探一次头：既用于空判断，也用于文件名前缀（生成器也能工作）
    first = next(items, None)
    if first is None:
        raise err.no_files()

    if total is None:
        raise err.invalid_param("传入生成器时必须提供 source_count")

    pages = image_utils.iter_page_images(
        chain((first,), items),
        mode,
        usable_ratio=pdf_builder.usable_ratio(cfg.margin_pt),
        quality=cfg.jpeg_quality,
        cfg=cfg,
    )

    output_path = storage.new_pdf_path(cfg, prefix=pdf_title or first[0])
    page_count = pdf_builder.build_pdf(
        pages,
        output_path,
        engine=cfg.pdf_engine,
        margin_pt=cfg.margin_pt,
        title=pdf_title or "AI 拍纸立得",
    )

    result = ConvertResult(
        path=output_path,
        page_count=page_count,
        size_bytes=storage.path_size(output_path),
        source_count=total,
        expires_at=storage.expires_at(cfg),
    )
    logger.info(
        "合成完成 file=%s 图片=%d 页数=%d 体积=%.2fMB",
        result.file_name,
        result.source_count,
        result.page_count,
        result.size_bytes / 1024 / 1024,
    )
    return result


def _safe_len(raw_files: Iterable[Tuple[str, bytes]]) -> Optional[int]:
    """序列才能取长度；生成器返回 None，由调用方显式传 source_count。"""
    try:
        return len(raw_files)  # type: ignore[arg-type]
    except TypeError:
        return None


def _iter_raw_items(
    raw_files: Iterable[Tuple[str, bytes]],
    rotations: Sequence[int],
) -> Iterator[Tuple[str, bytes, int]]:
    """把 (文件名, 字节) 与旋转角度配成三元组，交给图片层惰性处理。"""
    for index, (name, raw) in enumerate(raw_files):
        angle = rotations[index] if index < len(rotations) else 0
        yield name, raw, angle


def merge_uploads(
    files: Optional[Sequence[UploadFile]],
    single: Optional[UploadFile],
) -> List[UploadFile]:
    """兼容两种前端写法：字段 files（多文件）与字段 file（单文件，小程序常用）。"""
    merged: List[UploadFile] = []
    for item in list(files or []):
        if item is not None and (item.filename or "").strip():
            merged.append(item)
    if single is not None and (single.filename or "").strip():
        merged.append(single)
    return merged
