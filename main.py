"""AI 拍纸立得 —— FastAPI 服务入口。

启动方式：
    # 开发
    python main.py
    # 生产（推荐交给 systemd / supervisor 管理）
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.background import cleanup as cleanup_worker
from app.config import settings
from app.core import diagnostics
from app.core.exceptions import register_exception_handlers
from app.routers import convert
from app.schemas.pdf import HealthResponse
from app.services import pdf_builder, storage

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("aipdf")

# StaticFiles 挂载时会校验目录是否存在，因此先创建
storage.ensure_dirs(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("=" * 62)
    logger.info("%s v%s 启动", settings.app_name, settings.app_version)
    logger.info("PDF 引擎      : %s", pdf_builder.resolve_engine(settings.pdf_engine))
    logger.info("静态资源目录  : %s", settings.static_dir)
    logger.info("PDF 输出目录  : %s", settings.pdf_dir)
    logger.info("暂存目录      : %s", settings.upload_dir)
    logger.info("公网地址      : %s", settings.public_base_url or "（未配置，按请求 Host 推导）")
    logger.info("CORS 白名单   : %s", settings.cors_origins_list)
    for warning in diagnostics.collect(force=True):
        logger.warning("配置自检      : %s", warning)
    if not diagnostics.collect():
        logger.info("配置自检      : 通过")
    logger.info("=" * 62)

    task, stop_event = cleanup_worker.start_cleanup_task(settings)
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        logger.info("服务已停止")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="微信小程序「AI 拍纸立得」后端：多张图片按上传顺序合成 A4 PDF。",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ------------------------------ CORS ------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=settings.cors_allow_credentials and not settings.allow_all_origins,
    allow_methods=settings.cors_methods_list,
    allow_headers=settings.cors_headers_list,
    expose_headers=["Content-Disposition"],  # 让前端能读到下载文件名
    max_age=settings.cors_max_age,
)

# 注意：allow_origins=["*"] 时不能同时 allow_credentials=True（浏览器会拒绝），
# 上面已自动规避；生产环境请把 CORS_ALLOW_ORIGINS 配置成具体域名。


# ------------------------ 请求体大小保护 ---------------------------
@app.middleware("http")
async def limit_body_size_middleware(request: Request, call_next):
    """在读取请求体之前就用 Content-Length 拦掉超大请求，避免内存被打爆。"""
    raw_length = request.headers.get("content-length")
    if raw_length and raw_length.isdigit() and int(raw_length) > settings.max_request_body_bytes:
        return JSONResponse(
            status_code=413,
            content={
                "code": "REQUEST_TOO_LARGE",
                "message": f"请求体超过上限 {settings.max_request_body_mb}MB",
                "detail": None,
            },
        )
    return await call_next(request)

# --------------------------- 访问日志 -----------------------------
@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        cost = (time.perf_counter() - started) * 1000
        logger.exception("请求异常 %s %s (%.1fms)", request.method, request.url.path, cost)
        raise
    cost = (time.perf_counter() - started) * 1000
    # 静态文件访问量大，降级为 DEBUG
    level = logging.DEBUG if request.url.path.startswith("/static") else logging.INFO
    logger.log(
        level,
        "%s %s -> %d (%.1fms) client=%s",
        request.method,
        request.url.path,
        response.status_code,
        cost,
        request.client.host if request.client else "-",
    )
    return response


# --------------------------- 异常处理 -----------------------------
register_exception_handlers(app)

# --------------------------- 静态文件 -----------------------------
# 生成的 PDF 落盘在 static/pdfs/，可通过 https://域名/static/pdfs/xxx.pdf 直接访问
app.mount("/static", StaticFiles(directory=str(settings.static_dir), html=False), name="static")

# ---------------------------- 路由 --------------------------------
app.include_router(convert.router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse(
        {
            "app": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "convert_api": f"{settings.api_prefix}/convert-to-pdf",
            "convert_base64_api": f"{settings.api_prefix}/convert-to-pdf-base64",
            "upload_api": f"{settings.api_prefix}/upload-image",
            "convert_by_ids_api": f"{settings.api_prefix}/convert-to-pdf-by-ids",
            "static_prefix": settings.static_url_prefix,
        }
    )


@app.get("/health", response_model=HealthResponse, tags=["系统"], summary="健康检查")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        pdf_engine=pdf_builder.resolve_engine(settings.pdf_engine),
        public_base_url=settings.public_base_url,
        warnings=diagnostics.collect(),
    )


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
