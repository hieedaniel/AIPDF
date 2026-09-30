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
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

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

    return convert_streams_to_pdf(raw_files, page_mode=page_mode, pdf_title=pdf_title, cfg=cfg)


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
    page_mode: Optional[str] = None,
    pdf_title: Optional[str] = None,
    cfg: Settings = default_settings,
) -> ConvertResult:
    """分张上传流程第二步：按传入的 image_id 顺序合并为一个 PDF。"""
    if not image_ids:
        raise err.no_files()
    if len(image_ids) > cfg.max_file_count:
        raise err.too_many_files(cfg.max_file_count)

    return await anyio.to_thread.run_sync(
        lambda: _merge_temp_images(image_ids, page_mode=page_mode, pdf_title=pdf_title, cfg=cfg)
    )


def _merge_temp_images(
    image_ids: Sequence[str],
    *,
    page_mode: Optional[str],
    pdf_title: Optional[str],
    cfg: Settings,
) -> ConvertResult:
    raw_files: List[Tuple[str, bytes]] = []
    total_bytes = 0
    for image_id in image_ids:
        path = storage.resolve_upload_path(cfg, image_id)
        if not path.is_file():
            raise err.image_not_found(image_id)
        data = path.read_bytes()
        total_bytes += len(data)
        if total_bytes > cfg.max_total_size_bytes:
            raise err.total_too_large(cfg.max_total_size_mb)
        raw_files.append((path.name, data))

    try:
        result = convert_streams_to_pdf(
            raw_files, page_mode=page_mode, pdf_title=pdf_title, cfg=cfg
        )
    except Exception:
        # 失败时保留暂存图，方便客户端直接重试（30 分钟后由后台任务兜底清理）
        logger.warning("合并失败，保留 %d 张暂存原图供重试", len(image_ids))
        raise

    # 成功即清理，不多占磁盘
    for image_id in image_ids:
        storage.remove_upload(cfg, image_id)
    return result


def convert_streams_to_pdf(
    raw_files: Sequence[Tuple[str, bytes]],
    *,
    page_mode: Optional[str] = None,
    pdf_title: Optional[str] = None,
    cfg: Settings = default_settings,
) -> ConvertResult:
    """同步版本：入参为 [(原始文件名, 图片字节), ...]，按顺序合成 PDF。"""
    if not raw_files:
        raise err.no_files()

    mode = (page_mode or cfg.page_mode or "fit").strip().lower()
    if mode not in PAGE_MODES:
        raise err.invalid_param(f"page_mode 只能是 {PAGE_MODES} 之一，收到：{page_mode!r}")

    ratio = pdf_builder.usable_ratio(cfg.margin_pt)
    pages: List[image_utils.PageImage] = []

    for name, raw in raw_files:
        image = image_utils.decode_image(raw, name, cfg=cfg)
        try:
            pages.extend(
                image_utils.split_pages(
                    image,
                    mode,
                    usable_ratio=ratio,
                    quality=cfg.jpeg_quality,
                )
            )
        finally:
            image.close()

    if not pages:
        raise err.pdf_build_failed("图片处理结果为空")

    output_path = storage.new_pdf_path(cfg, prefix=pdf_title or raw_files[0][0])
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
        source_count=len(raw_files),
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
