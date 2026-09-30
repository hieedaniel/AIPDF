"""全局配置。

所有配置都可以通过环境变量或项目根目录的 .env 文件覆盖，
环境变量优先于 .env。字段名大小写不敏感（MAX_FILE_SIZE_MB / max_file_size_mb 均可）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/ 的上一级）
BASE_DIR: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 基础信息 ----------------
    app_name: str = "AI 拍纸立得 API"
    app_version: str = "1.0.0"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # ---------------- 静态文件 / 存储 ----------------
    # PDF 落盘目录（默认为 项目根/static）
    static_dir: Path = BASE_DIR / "static"
    # static_dir 下的子目录，生成的 PDF 放在这里
    pdf_subdir: str = "pdfs"
    # 运行时目录（暂存上传的原图，不对外暴露）
    var_dir: Path = BASE_DIR / "var"
    upload_subdir: str = "uploads"
    # 暂存原图的保留时长（分钟），合成后立即删除，这里是兜底
    upload_ttl_minutes: int = 30
    # 对外访问域名，例如 https://api.yourdomain.com
    # 留空则根据请求的 Host 自动推导（生产环境建议显式配置）
    public_base_url: Optional[str] = None
    # 生成的 PDF 保留时长（小时），到期由后台任务清理
    pdf_ttl_hours: int = 24
    # 后台清理任务的执行间隔（分钟）
    cleanup_interval_minutes: int = 60

    # ---------------- 上传限制 ----------------
    max_file_size_mb: int = 15      # 单张图片大小上限
    max_total_size_mb: int = 80     # 单次请求所有图片总大小上限
    max_request_body_mb: int = 120  # 单次 HTTP 请求体上限（base64 场景留出 1/3 余量）
    max_file_count: int = 20        # 单次请求最多图片张数
    min_image_side: int = 16        # 图片最小边（像素），过小视为损坏
    max_image_side: int = 4096      # 长边超过该值时等比缩小，控制内存与体积

    # ---------------- PDF 生成 ----------------
    # auto: 优先 PyMuPDF，缺失时自动回退 ReportLab
    pdf_engine: Literal["auto", "pymupdf", "reportlab"] = "auto"
    # fit  : 每张图片独占一页（默认）
    # split: 长截图按 A4 比例自动切成多页
    page_mode: Literal["fit", "split"] = "fit"
    page_margin_mm: float = 0.0     # 页面留白（毫米）
    jpeg_quality: int = 90          # 图片压缩质量 1-95

    # ---------------- CORS ----------------
    # 逗号分隔，例如 "https://a.com,https://b.com"；"*" 表示放行全部
    cors_allow_origins: str = "*"
    cors_allow_methods: str = "*"
    cors_allow_headers: str = "*"
    cors_allow_credentials: bool = False
    cors_max_age: int = 600

    # ---------------- 派生属性 ----------------
    @property
    def pdf_dir(self) -> Path:
        """PDF 落盘的绝对目录。"""
        return self.static_dir / self.pdf_subdir

    @property
    def upload_dir(self) -> Path:
        """“分张上传 → 一次合并”流程的暂存目录，不在 static 下，不会被公开访问。"""
        return self.var_dir / self.upload_subdir

    @property
    def static_url_prefix(self) -> str:
        """静态资源 URL 前缀，例如 /static/pdfs。"""
        return f"/static/{self.pdf_subdir}".rstrip("/")

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def max_total_size_bytes(self) -> int:
        return self.max_total_size_mb * 1024 * 1024

    @property
    def max_request_body_bytes(self) -> int:
        return self.max_request_body_mb * 1024 * 1024

    @property
    def margin_pt(self) -> float:
        """页边距，单位 pt（1pt = 1/72 inch）。"""
        return max(self.page_margin_mm, 0.0) * 72.0 / 25.4

    @staticmethod
    def _split_csv(raw: str) -> List[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        values = self._split_csv(self.cors_allow_origins) or ["*"]
        return values

    @property
    def cors_methods_list(self) -> List[str]:
        return self._split_csv(self.cors_allow_methods) or ["*"]

    @property
    def cors_headers_list(self) -> List[str]:
        return self._split_csv(self.cors_allow_headers) or ["*"]

    @property
    def allow_all_origins(self) -> bool:
        return "*" in self.cors_origins_list


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置。测试中如需切换配置请调用 get_settings.cache_clear()。"""
    return Settings()


settings: Settings = get_settings()
