"""图片转 PDF 相关接口。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile, status

from app.config import settings
from app.core import exceptions as err
from app.schemas.pdf import (
    Base64ConvertRequest,
    ConvertByIdsRequest,
    ConvertResponse,
    ErrorResponse,
    UploadImageResponse,
)
from app.services import pdf_service, storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["拍纸立得"])


@router.post(
    "/convert-to-pdf",
    response_model=ConvertResponse,
    status_code=status.HTTP_200_OK,
    summary="多张图片按上传顺序合成 A4 PDF",
    description=(
        "接收 1~N 张图片（multipart/form-data），按上传顺序合成一个 A4 PDF，"
        "返回可直接下载/预览的静态资源地址。\n\n"
        "- 多文件字段名：`files`（可重复）\n"
        "- 单文件字段名：`file`（兼容微信小程序 `wx.uploadFile` 单文件限制）\n"
        "- `page_mode=fit`：每张图片独占一页（默认）；`page_mode=split`：长截图自动分页\n"
    ),
    responses={
        400: {"model": ErrorResponse, "description": "参数错误 / 图片无法解析"},
        413: {"model": ErrorResponse, "description": "文件过大"},
        415: {"model": ErrorResponse, "description": "不支持的图片格式"},
        422: {"model": ErrorResponse, "description": "参数校验失败"},
        500: {"model": ErrorResponse, "description": "PDF 生成失败"},
    },
)
async def convert_to_pdf(
    request: Request,
    files: Optional[List[UploadFile]] = File(
        default=None,
        description="待合成的图片，可重复该字段；按传入顺序排版",
    ),
    file: Optional[UploadFile] = File(
        default=None,
        description="单文件上传入口（与 files 二选一或同时使用）",
    ),
    page_mode: Optional[str] = Form(
        default=None,
        description="fit=每张图一页（默认），split=长图按 A4 比例切片分页",
    ),
    pdf_title: Optional[str] = Form(
        default=None,
        description="PDF 元数据标题，同时用于生成文件名前缀",
    ),
) -> ConvertResponse:
    uploads = pdf_service.merge_uploads(files, file)
    if not uploads:
        raise err.no_files()

    result = await pdf_service.convert_uploads_to_pdf(
        uploads,
        page_mode=page_mode,
        pdf_title=pdf_title,
        cfg=settings,
    )

    url = storage.build_public_url(request, result.path, settings)
    return ConvertResponse(
        code=0,
        message="ok",
        pdf_url=url,
        file_name=result.file_name,
        page_count=result.page_count,
        size_bytes=result.size_bytes,
        source_count=result.source_count,
        expires_at=result.expires_at,
    )


@router.post(
    "/upload-image",
    response_model=UploadImageResponse,
    summary="【分张上传】上传单张图片并暂存，返回 image_id",
    description=(
        "微信小程序 `wx.uploadFile` 一次只能传一个文件，因此推荐流程为：\n\n"
        "1. 按顺序循环调用本接口上传每张图，收集 `image_id`；\n"
        "2. 把 `image_id` 数组按期望的排版顺序提交给 `/convert-to-pdf-by-ids`，一次合成。\n\n"
        "图片会先规范化（EXIF 纠正 / 转 RGB / JPEG）再暂存到非公开目录，"
        "合并成功后立即删除，未合并的由后台任务按 TTL 清理。"
    ),
    responses={
        400: {"model": ErrorResponse, "description": "参数错误 / 图片无法解析"},
        413: {"model": ErrorResponse, "description": "文件过大"},
        415: {"model": ErrorResponse, "description": "不支持的图片格式"},
    },
)
async def upload_image(
    file: Optional[UploadFile] = File(default=None, description="单张图片，字段名 file"),
    image: Optional[UploadFile] = File(default=None, description="单张图片，字段名 image（二选一）"),
) -> UploadImageResponse:
    upload = file if _has_file(file) else image
    if not _has_file(upload):
        raise err.no_files()

    image_id, path = await pdf_service.save_temp_image(upload, cfg=settings)
    return UploadImageResponse(
        code=0,
        message="ok",
        image_id=image_id,
        size_bytes=storage.path_size(path),
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=settings.upload_ttl_minutes),
    )


@router.post(
    "/convert-to-pdf-by-ids",
    response_model=ConvertResponse,
    summary="【分张上传】按 image_id 顺序合并为 A4 PDF",
    description=(
        "接收 `/upload-image` 返回的图片列表，按数组顺序排版合成一个 A4 PDF。\n\n"
        "- `image_ids: [\"...\"]`：仅按顺序合并；\n"
        "- `items: [{\"image_id\": \"...\", \"rotate\": 90}]`：额外支持顺时针旋转，"
        "在服务端旋转是无损的（不需要客户端重新编码）。"
    ),
    responses={
        400: {"model": ErrorResponse, "description": "image_id 非法 / 没有可合并的图片"},
        404: {"model": ErrorResponse, "description": "image_id 不存在或已过期"},
        413: {"model": ErrorResponse, "description": "总量超限"},
        500: {"model": ErrorResponse, "description": "PDF 生成失败"},
    },
)
async def convert_to_pdf_by_ids(
    request: Request,
    payload: ConvertByIdsRequest,
) -> ConvertResponse:
    items = payload.resolved_items()
    result = await pdf_service.convert_temp_images_to_pdf(
        [item.image_id for item in items],
        rotations=[item.rotate for item in items],
        page_mode=payload.page_mode,
        pdf_title=payload.pdf_title,
        cfg=settings,
    )
    return ConvertResponse(
        code=0,
        message="ok",
        pdf_url=storage.build_public_url(request, result.path, settings),
        file_name=result.file_name,
        page_count=result.page_count,
        size_bytes=result.size_bytes,
        source_count=result.source_count,
        expires_at=result.expires_at,
    )


def _has_file(upload: Optional[UploadFile]) -> bool:
    return upload is not None and bool((upload.filename or "").strip())


@router.post(
    "/convert-to-pdf-base64",
    response_model=ConvertResponse,
    summary="多张图片（base64 JSON）合成 A4 PDF",
    description=(
        "与 `/convert-to-pdf` 等价，但请求体为 JSON，适合微信小程序"
        "（`wx.uploadFile` 单次只能上传一个文件，直接传 base64 数组更省事）。\n\n"
        "`images[].data` 支持纯 base64 或 `data:image/jpeg;base64,xxxx` 形式。"
    ),
    responses={
        400: {"model": ErrorResponse, "description": "参数错误 / 图片无法解析"},
        413: {"model": ErrorResponse, "description": "文件过大"},
        415: {"model": ErrorResponse, "description": "不支持的图片格式"},
        500: {"model": ErrorResponse, "description": "PDF 生成失败"},
    },
)
async def convert_to_pdf_base64(
    request: Request,
    payload: Base64ConvertRequest,
) -> ConvertResponse:
    images = [
        (item.file_name or f"image-{index}", item.data)
        for index, item in enumerate(payload.images, start=1)
    ]
    result = await pdf_service.convert_base64_to_pdf(
        images,
        page_mode=payload.page_mode,
        pdf_title=payload.pdf_title,
        cfg=settings,
    )
    return ConvertResponse(
        code=0,
        message="ok",
        pdf_url=storage.build_public_url(request, result.path, settings),
        file_name=result.file_name,
        page_count=result.page_count,
        size_bytes=result.size_bytes,
        source_count=result.source_count,
        expires_at=result.expires_at,
    )
