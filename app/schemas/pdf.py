"""接口出入参模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class ConvertResponse(BaseModel):
    """POST /api/v1/convert-to-pdf 成功响应。"""

    code: int = Field(default=0, description="0 表示成功")
    message: str = Field(default="ok")
    pdf_url: str = Field(description="PDF 公网可访问地址")
    file_name: str = Field(description="PDF 文件名（不含路径）")
    page_count: int = Field(description="PDF 页数")
    size_bytes: int = Field(description="PDF 文件大小（字节）")
    source_count: int = Field(description="参与合成的图片张数")
    expires_at: Optional[datetime] = Field(default=None, description="预计清理时间（UTC）")

    model_config = {
        "json_schema_extra": {
            "example": {
                "code": 0,
                "message": "ok",
                "pdf_url": "https://yourdomain.com/static/pdfs/20250101_ab12cd34ef56.pdf",
                "file_name": "20250101_ab12cd34ef56.pdf",
                "page_count": 3,
                "size_bytes": 524288,
                "source_count": 3,
                "expires_at": "2025-01-02T08:00:00Z",
            }
        }
    }


class ErrorResponse(BaseModel):
    """统一错误响应。"""

    code: str = Field(description="错误码，如 FILE_TOO_LARGE")
    message: str = Field(description="可直接展示给用户的提示")
    detail: Optional[object] = Field(default=None, description="调试细节")


class Base64Image(BaseModel):
    """base64 形式的图片。"""

    data: str = Field(
        min_length=1,
        description="图片内容：纯 base64，或 `data:image/jpeg;base64,xxxx` 形式",
    )
    file_name: Optional[str] = Field(default=None, description="仅用于日志与文件名前缀")


class Base64ConvertRequest(BaseModel):
    """POST /api/v1/convert-to-pdf-base64 请求体。

    微信小程序 `wx.uploadFile` 一次只能传一个文件，用 JSON 传 base64 更省事。
    """

    images: List[Base64Image] = Field(min_length=1, description="按排版顺序排列的图片")
    page_mode: Optional[str] = Field(default=None, description="fit | split")
    pdf_title: Optional[str] = Field(default=None, description="PDF 标题 / 文件名前缀")


class UploadImageResponse(BaseModel):
    """POST /api/v1/upload-image 成功响应。"""

    code: int = Field(default=0, description="0 表示成功")
    message: str = Field(default="ok")
    image_id: str = Field(description="暂存图片标识，用于后续合并")
    size_bytes: int = Field(description="规范化后的图片大小")
    expires_at: Optional[datetime] = Field(default=None, description="暂存过期时间（UTC）")


class ConvertByIdsItem(BaseModel):
    """单张待合并图片：image_id + 可选的顺时针旋转角度。"""

    image_id: str = Field(min_length=1, description="/upload-image 返回的图片标识")
    rotate: int = Field(
        default=0,
        description="顺时针旋转角度，仅支持 0 / 90 / 180 / 270（其他值会被吸附到最近的 90°）",
    )


class ConvertByIdsRequest(BaseModel):
    """POST /api/v1/convert-to-pdf-by-ids 请求体。

    配合 wx.uploadFile 的分张上传：先逐张 /upload-image 拿到 image_id，
    再按期望的排版顺序把 id 数组一次性提交。

    - 只排序：`{"image_ids": ["...", "..."]}`
    - 排序 + 旋转：`{"items": [{"image_id": "...", "rotate": 90}]}`（优先使用 items）
    """

    image_ids: Optional[List[str]] = Field(
        default=None, min_length=1, description="按排版顺序排列的 image_id（与 items 二选一）"
    )
    items: Optional[List[ConvertByIdsItem]] = Field(
        default=None, min_length=1, description="带旋转角度的图片列表，优先级高于 image_ids"
    )
    page_mode: Optional[str] = Field(default=None, description="fit | split")
    pdf_title: Optional[str] = Field(default=None, description="PDF 标题 / 文件名前缀")

    @model_validator(mode="after")
    def _require_images(self) -> ConvertByIdsRequest:
        if not self.items and not self.image_ids:
            raise ValueError("必须提供非空的 items 或 image_ids")
        return self

    def resolved_items(self) -> List[ConvertByIdsItem]:
        """归一成带旋转角度的列表（items 优先，兼容旧的 image_ids）。"""
        if self.items:
            return list(self.items)
        return [ConvertByIdsItem(image_id=image_id) for image_id in (self.image_ids or [])]


class LimitsInfo(BaseModel):
    """服务端生效的限制值。

    `/health` 会把它返回给客户端，小程序据此限制一次选图的张数 —— 
    避免「手机允许 30 张、服务端只收 20 张」这类只能到用户那里才暴露的错配。
    """

    max_file_count: int = Field(description="单次合成最多张数")
    max_file_size_mb: int = Field(description="单张图片大小上限（MB）")
    max_total_size_mb: int = Field(description="单次合成图片总大小上限（MB）")


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    version: str
    pdf_engine: str
    public_base_url: Optional[str] = Field(
        default=None, description="实际生效的对外地址前缀（空表示按请求 Host 推导）"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="启动自检告警；非空表示“服务能跑但功能会不正常”，例如 PUBLIC_BASE_URL 未改",
    )
    limits: LimitsInfo = Field(description="生效中的各项限制，客户端可据此自适应")
