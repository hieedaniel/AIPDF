"""启动自检：把「服务跑得起来、但功能不正常」的配置坑直接暴露在 /health 里。

背景：`PUBLIC_BASE_URL` 忘了改 / 还是占位域名时，后端合成 PDF 一切正常，
但返回的 `pdf_url` 指向一个不存在的域名 —— 客户端只看到一个
`downloadFile:fail timeout`，非常难排查。这类问题在这里统一体检。

`/health` 响应里的 `warnings` 数组就是本模块的输出，部署脚本与
`scripts/check-server.ps1` 都会读它。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from app.config import Settings
from app.config import settings as default_settings

# 模板里出现过的占位域名，命中即视为「忘了改」
PLACEHOLDER_HOSTS = ("yourdomain.com", "your-domain.com", "example.com", "example.org")

# 日志里提示修复方式的统一文案
_FIX_HINT = "修复：改 PUBLIC_BASE_URL=https://你的域名（改完必须重建容器，docker restart 不重读 env）"


def _is_placeholder(url: str) -> bool:
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    return any(host == p or host.endswith("." + p) for p in PLACEHOLDER_HOSTS)


def _dir_writable(path: Path) -> bool:
    """真实写一个探针文件，而不是看权限位（bind mount 场景权限位没有意义）。"""
    probe = path / ".write_probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def check_public_base_url(cfg: Settings) -> List[str]:
    url = (cfg.public_base_url or "").strip()
    if not url:
        return [
            "未配置 PUBLIC_BASE_URL：将按请求 Host 推导对外地址，"
            "经 Nginx/CDN 反代时生成的 pdf_url 可能不可访问。" + _FIX_HINT,
        ]
    if _is_placeholder(url):
        return [
            f"PUBLIC_BASE_URL 仍是占位值 {url}：接口返回的 pdf_url 指向不存在的域名，"
            "客户端会报 downloadFile:fail。" + _FIX_HINT,
        ]
    if not url.startswith("https://"):
        return [
            f"PUBLIC_BASE_URL 不是 https（{url}）：微信小程序 downloadFile 要求 HTTPS。" + _FIX_HINT,
        ]
    return []


def check_dirs(cfg: Settings) -> List[str]:
    warnings: List[str] = []
    for label, path in (("STATIC_DIR", Path(cfg.static_dir)), ("VAR_DIR", Path(cfg.var_dir))):
        if not _dir_writable(path):
            warnings.append(
                f"{label}={path} 不可写（当前 uid={os.getuid() if hasattr(os, 'getuid') else '?'}）："
                "上传/合成会返回 STORAGE_NOT_WRITABLE。"
                "bind mount 场景请执行 chown -R 10001:10001 <宿主机目录> 后重启容器",
            )
    return warnings


def check_engine(cfg: Settings) -> List[str]:
    if cfg.pdf_engine != "auto":
        return []
    # 只有显式要求 auto 时才提示，避免用户自己选了 reportlab 还被唠叨
    from app.services import pdf_builder

    if pdf_builder.resolve_engine("auto") != "pymupdf":
        return [
            "PyMuPDF 不可用，已回退 ReportLab：中文/大图排版效果与体积会差一些，"
            "建议使用带 PyMuPDF 的官方镜像",
        ]
    return []


def _compute(cfg: Settings) -> List[str]:
    warnings: List[str] = []
    warnings += check_public_base_url(cfg)
    warnings += check_dirs(cfg)
    warnings += check_engine(cfg)
    return warnings


_cache: Optional[List[str]] = None


def collect(*, force: bool = False, cfg: Optional[Settings] = None) -> List[str]:
    """返回自检告警列表（进程内缓存，避免每次 /health 都写探针文件）。"""
    global _cache
    if _cache is None or force:
        _cache = _compute(cfg or default_settings)
    return _cache


def reset_cache() -> None:
    """供测试使用。"""
    global _cache
    _cache = None
