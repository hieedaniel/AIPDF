"""统一的业务异常与全局异常处理。

所有返回给客户端的错误都是同一个 JSON 结构：

    {"code": "FILE_TOO_LARGE", "message": "单张图片不能超过 15MB", "detail": null}
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """业务异常：会被全局处理器转换成标准 JSON 错误响应。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


# ---- 常用错误工厂（保持错误码集中管理，方便小程序端做多语言/提示） ----
def no_files() -> ApiError:
    return ApiError(400, "NO_FILES", "请至少上传一张图片")


def too_many_files(limit: int) -> ApiError:
    return ApiError(400, "TOO_MANY_FILES", f"单次最多上传 {limit} 张图片")


def invalid_param(message: str, detail: Optional[Any] = None) -> ApiError:
    return ApiError(400, "INVALID_PARAM", message, detail)


def empty_file(name: str) -> ApiError:
    return ApiError(400, "EMPTY_FILE", f"文件 {name} 内容为空")


def file_too_large(name: str, limit_mb: float) -> ApiError:
    return ApiError(413, "FILE_TOO_LARGE", f"图片 {name} 超过单张上限 {limit_mb:g}MB")


def total_too_large(limit_mb: float) -> ApiError:
    return ApiError(413, "TOTAL_TOO_LARGE", f"本次上传总大小超过 {limit_mb:g}MB")


def unsupported_type(name: str, detected: Optional[str] = None) -> ApiError:
    hint = f"（识别为 {detected}）" if detected else ""
    return ApiError(
        415,
        "UNSUPPORTED_TYPE",
        f"文件 {name} 不是受支持的图片格式{hint}，请上传 JPG/PNG/WebP/BMP/GIF/TIFF",
    )


def decode_failed(name: str, reason: str = "") -> ApiError:
    detail = f"：{reason}" if reason else ""
    return ApiError(400, "IMAGE_DECODE_FAILED", f"图片 {name} 解析失败{detail}")


def image_too_small(name: str, min_side: int) -> ApiError:
    return ApiError(400, "IMAGE_TOO_SMALL", f"图片 {name} 分辨率过低，最小边长需不小于 {min_side}px")


def pdf_build_failed(reason: str = "") -> ApiError:
    detail = f"：{reason}" if reason else ""
    return ApiError(500, "PDF_BUILD_FAILED", f"PDF 生成失败{detail}")


def invalid_image_id(value: object) -> ApiError:
    return ApiError(400, "INVALID_IMAGE_ID", f"image_id 非法：{value!r}")


def image_not_found(image_id: str) -> ApiError:
    return ApiError(
        404,
        "IMAGE_NOT_FOUND",
        "暂存图片已过期或不存在，请重新上传",
        {"image_id": image_id},
    )


def storage_not_writable(reason: str = "") -> ApiError:
    """存储目录不可写（最常见的是 bind mount 属主与容器内 uid 不一致）。"""
    detail = f"：{reason}" if reason else ""
    return ApiError(
        500,
        "STORAGE_NOT_WRITABLE",
        "服务器存储目录不可写，请检查容器挂载目录权限" + detail,
    )


# ------------------------- 全局异常处理器 -------------------------
def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        logger.warning("业务异常 %s %s -> [%s] %s", request.method, request.url.path, exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": f"HTTP_{exc.status_code}", "message": str(exc.detail), "detail": None},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_PARAM", "message": "请求参数校验失败", "detail": detail},
        )

    @app.exception_handler(PermissionError)
    async def _permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
        # 这类错误几乎全是「bind mount 的宿主机目录属主与容器内 uid 不一致」，
        # 给出可操作的提示，避免只看到一个笼统的“服务器内部错误”。
        logger.exception("存储不可写 %s %s", request.method, request.url.path)
        err = storage_not_writable(str(exc))
        return JSONResponse(
            status_code=err.status_code,
            content={"code": err.code, "message": err.message, "detail": err.detail},
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常 %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误，请稍后重试", "detail": None},
        )
