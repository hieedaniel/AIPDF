"""文件落盘、公网 URL 拼接与过期清理。"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Request

from app.config import Settings
from app.config import settings as default_settings
from app.core import exceptions as err

logger = logging.getLogger(__name__)

_SAFE_SLUG = re.compile(r"[^0-9A-Za-z_-]+")
_PDF_SUFFIX = ".pdf"
# 暂存原图的 image_id：32 位小写十六进制（uuid4().hex），白名单式校验，天然防路径穿越
_IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_UPLOAD_SUFFIX = ".jpg"


def ensure_dirs(cfg: Settings = default_settings) -> None:
    """确保静态目录与暂存目录存在（StaticFiles 在挂载时会校验目录）。"""
    cfg.pdf_dir.mkdir(parents=True, exist_ok=True)
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)


def safe_slug(raw: Optional[str], max_len: int = 40) -> str:
    """把用户提供的文件名清洗成安全 slug，避免路径穿越/非法字符。"""
    if not raw:
        return ""
    stem = Path(raw).stem
    slug = _SAFE_SLUG.sub("", stem.replace(" ", "_"))
    return slug[:max_len]


def new_pdf_path(cfg: Settings = default_settings, prefix: Optional[str] = None) -> Path:
    """生成唯一文件名：<日期>_<slug>_<随机>.pdf"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = safe_slug(prefix)
    parts = [stamp]
    if slug:
        parts.append(slug)
    parts.append(uuid.uuid4().hex[:16])
    return cfg.pdf_dir / ("_".join(parts) + _PDF_SUFFIX)


def new_upload_path(cfg: Settings = default_settings) -> Path:
    """给一张待合并的原图分配暂存路径：var/uploads/<32位十六进制>.jpg"""
    return cfg.upload_dir / (uuid.uuid4().hex + _UPLOAD_SUFFIX)


def resolve_upload_path(cfg: Settings, image_id: str) -> Path:
    """根据 image_id 定位暂存原图；非法 id 直接拒绝（防路径穿越）。"""
    if not isinstance(image_id, str) or not _IMAGE_ID_RE.match(image_id):
        raise err.invalid_image_id(image_id)
    return cfg.upload_dir / (image_id + _UPLOAD_SUFFIX)


def remove_upload(cfg: Settings, image_id: str) -> None:
    """删除单个暂存原图，失败不抛异常。"""
    try:
        resolve_upload_path(cfg, image_id).unlink(missing_ok=True)
    except Exception:
        logger.warning("暂存原图删除失败：%s", image_id)


def build_public_url(request: Request, path: Path, cfg: Settings = default_settings) -> str:
    """拼出公网可访问地址。

    优先使用 PUBLIC_BASE_URL（生产环境务必配置，避免被 Host 头欺骗）；
    未配置时回退到请求自身的 base_url（Nginx 反代需传递 X-Forwarded-Proto）。
    """
    if cfg.public_base_url:
        base = cfg.public_base_url.strip().rstrip("/")
    else:
        base = str(request.base_url).rstrip("/")
    return f"{base}{cfg.static_url_prefix}/{path.name}"


def expires_at(cfg: Settings = default_settings) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=cfg.pdf_ttl_hours)


def path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def cleanup_expired(cfg: Settings = default_settings, *, now: Optional[float] = None) -> int:
    """删除超过 TTL 的 PDF（含残留的 .part 半成品），返回删除数量。"""
    directory = cfg.pdf_dir
    if not directory.is_dir():
        return 0

    now = time.time() if now is None else now
    ttl_seconds = max(cfg.pdf_ttl_hours, 1) * 3600
    # .part 是生成中的临时文件，超过 1 小时视为异常残留
    part_ttl_seconds = 3600
    removed = 0

    for file in directory.iterdir():
        try:
            if not file.is_file():
                continue
            is_part = file.name.endswith(".part")
            if not (file.name.endswith(_PDF_SUFFIX) or is_part):
                continue
            limit = part_ttl_seconds if is_part else ttl_seconds
            if now - file.stat().st_mtime > limit:
                file.unlink()
                removed += 1
        except OSError as exc:  # 单个文件失败不影响整体
            logger.warning("清理文件失败 %s: %s", file, exc)

    if removed:
        logger.info("过期清理完成，删除 %d 个文件", removed)
    return removed


def cleanup_uploads(cfg: Settings = default_settings, *, now: Optional[float] = None) -> int:
    """删除超过 upload_ttl_minutes 的暂存原图（合成成功后已主动删除，这里是兜底）。"""
    directory = cfg.upload_dir
    if not directory.is_dir():
        return 0

    now = time.time() if now is None else now
    ttl_seconds = max(cfg.upload_ttl_minutes, 1) * 60
    removed = 0
    for file in directory.iterdir():
        try:
            if not file.is_file():
                continue
            if now - file.stat().st_mtime > ttl_seconds:
                file.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("清理暂存图片失败 %s: %s", file, exc)

    if removed:
        logger.info("暂存清理完成，删除 %d 张原图", removed)
    return removed
