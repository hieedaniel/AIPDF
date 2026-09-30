"""把一组 JPEG 图片按顺序合成为 A4 PDF。

提供两个引擎，二进制接口一致：
- PyMuPDF（fitz）：默认首选，体积小、速度快、纯 wheel 无需编译。
- ReportLab     ：PyMuPDF 安装失败时的兜底方案。

两种引擎最终输出都是 A4（595.28pt × 841.89pt）纵向页面，
每张图片等比缩放后居中放置，绝不拉伸变形。
"""

from __future__ import annotations

import importlib.util
import logging
import os
from io import BytesIO
from itertools import chain
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

from app.config import settings
from app.core.exceptions import ApiError as ApiError_
from app.core.exceptions import pdf_build_failed

logger = logging.getLogger(__name__)

# A4 尺寸（单位 pt，1pt = 1/72 inch）
A4_WIDTH_PT = 595.2755905511812
A4_HEIGHT_PT = 841.8897637795277

PageImage = Tuple[bytes, int, int]  # (jpeg 字节, 像素宽, 像素高)


def _fit_rect(img_w: int, img_h: int, margin_pt: float) -> Tuple[float, float, float, float]:
    """在 A4 页面内按原图宽高比等比缩放并居中，返回 (x0, y0, x1, y1)。"""
    avail_w = max(A4_WIDTH_PT - 2 * margin_pt, 1.0)
    avail_h = max(A4_HEIGHT_PT - 2 * margin_pt, 1.0)
    scale = min(avail_w / float(img_w), avail_h / float(img_h))
    width, height = img_w * scale, img_h * scale
    x0 = (A4_WIDTH_PT - width) / 2.0
    y0 = (A4_HEIGHT_PT - height) / 2.0
    return x0, y0, x0 + width, y0 + height


def load_pymupdf():
    """兼容导入：PyMuPDF >= 1.24.3 推荐 `import pymupdf`，旧版本只能 `import fitz`。"""
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        import fitz  # type: ignore

        return fitz


def _has_pymupdf() -> bool:
    return (
        importlib.util.find_spec("pymupdf") is not None
        or importlib.util.find_spec("fitz") is not None
    )


def resolve_engine(preferred: str = "auto") -> str:
    """决定实际使用的引擎，返回 'pymupdf' 或 'reportlab'。"""
    preferred = (preferred or "auto").lower()
    has_pymupdf = _has_pymupdf()
    has_reportlab = importlib.util.find_spec("reportlab") is not None

    if preferred == "pymupdf":
        if not has_pymupdf:
            raise pdf_build_failed("未安装 PyMuPDF，请 pip install PyMuPDF 或改用 PDF_ENGINE=reportlab")
        return "pymupdf"
    if preferred == "reportlab":
        if not has_reportlab:
            raise pdf_build_failed("未安装 ReportLab，请 pip install reportlab")
        return "reportlab"

    if has_pymupdf:
        return "pymupdf"
    if has_reportlab:
        logger.warning("未检测到 PyMuPDF，自动回退到 ReportLab")
        return "reportlab"
    raise pdf_build_failed("未安装任何 PDF 引擎（PyMuPDF / ReportLab）")


def build_pdf(
    pages: Iterable[PageImage],
    output_path: Path,
    *,
    engine: Optional[str] = None,
    margin_pt: Optional[float] = None,
    title: Optional[str] = None,
) -> int:
    """生成 PDF 并写入 output_path（先写 .part 再原子重命名，避免半成品被下载）。

    pages 可以是任意可迭代对象（含生成器）：图片是「取一页写一页」的，
    所以调用方可以惰性解码 —— 合成 30 张图时内存不会随张数线性增长。

    返回页数。
    """
    # 不 materialize：只探一次头，既能判空，也不破坏惰性
    iterator = iter(pages)
    try:
        first = next(iterator)
    except StopIteration:
        raise pdf_build_failed("没有可写入的图片") from None
    stream = chain((first,), iterator)

    engine_name = resolve_engine(engine or settings.pdf_engine)
    margin_pt = settings.margin_pt if margin_pt is None else margin_pt

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")

    try:
        if engine_name == "pymupdf":
            page_count = _build_with_pymupdf(stream, tmp_path, margin_pt, title)
        else:
            page_count = _build_with_reportlab(stream, tmp_path, margin_pt, title)
        os.replace(tmp_path, output_path)  # 原子替换
    except Exception as exc:
        logger.exception("PDF 生成失败 engine=%s", engine_name)
        for path in (tmp_path, output_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:  # Windows 下文件句柄可能还没释放，交给清理任务兜底
                logger.warning("临时文件删除失败：%s", path)
        if isinstance(exc, ApiError_):
            raise
        raise pdf_build_failed(str(exc)) from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise pdf_build_failed("生成的文件为空")
    return page_count


# --------------------------- PyMuPDF 引擎 ---------------------------
def _build_with_pymupdf(
    pages: Iterator[PageImage],
    output_path: Path,
    margin_pt: float,
    title: Optional[str],
) -> int:
    fitz = load_pymupdf()
    doc = fitz.open()
    try:
        for payload, width, height in pages:
            page = doc.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
            rect = fitz.Rect(*_fit_rect(width, height, margin_pt))
            page.insert_image(rect, stream=payload)
        doc.set_metadata(
            {
                "title": title or "AI 拍纸立得",
                "producer": f"AI 拍纸立得 ({settings.app_version})",
                "creator": settings.app_name,
            }
        )
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
        return doc.page_count
    finally:
        doc.close()


# --------------------------- ReportLab 引擎 ---------------------------
def _build_with_reportlab(
    pages: Iterator[PageImage],
    output_path: Path,
    margin_pt: float,
    title: Optional[str],
) -> int:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    c = rl_canvas.Canvas(str(output_path), pagesize=A4, pageCompression=1)
    if title:
        c.setTitle(title)
        c.setAuthor("AI 拍纸立得")
    count = 0
    for payload, width, height in pages:
        x0, y0, x1, y1 = _fit_rect(width, height, margin_pt)
        # ReportLab 原点在左下角；anchor='c' + preserveAspectRatio 保证居中不变形
        c.drawImage(
            ImageReader(BytesIO(payload)),
            x0,
            y0,
            width=x1 - x0,
            height=y1 - y0,
            preserveAspectRatio=True,
            anchor="c",
            mask=None,
        )
        c.showPage()
        count += 1
    c.save()
    return count


def usable_ratio(margin_pt: float) -> float:
    """可打印区域宽高比，split 模式据此决定单页容纳多少像素。"""
    avail_w = max(A4_WIDTH_PT - 2 * margin_pt, 1.0)
    avail_h = max(A4_HEIGHT_PT - 2 * margin_pt, 1.0)
    return avail_w / avail_h
