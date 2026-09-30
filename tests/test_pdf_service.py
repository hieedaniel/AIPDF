"""服务层测试：两个 PDF 引擎、过期清理、文件名安全。"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
from conftest import make_image
from PIL import Image

from app.config import settings
from app.core.exceptions import ApiError
from app.services import image_utils, pdf_builder, pdf_service, storage
from app.services.pdf_builder import load_pymupdf

fitz = load_pymupdf()


def _decode(data: bytes, name: str = "t.jpg") -> Image.Image:
    return image_utils.decode_image(data, name)


def test_sniff_format_detects_real_type():
    assert image_utils.sniff_format(make_image((64, 64), fmt="JPEG")) == "JPEG"
    assert image_utils.sniff_format(make_image((64, 64), fmt="PNG")) == "PNG"
    assert image_utils.sniff_format(b"hello world") is None


def test_decode_image_flattens_transparency_to_white():
    img = _decode(make_image((120, 120), fmt="PNG"), "a.png")
    assert img.mode == "RGB"
    # 左上角是透明区域，应被合成为白色而不是黑色
    assert img.getpixel((2, 2)) == (255, 255, 255)


def test_decode_image_downscales_large_image():
    big_width = settings.max_image_side + 1000
    img = _decode(make_image((big_width, 1500), fmt="JPEG"), "big.jpg")
    ratio = settings.max_image_side / big_width
    assert img.width == settings.max_image_side
    assert img.height == round(1500 * ratio)


@pytest.mark.parametrize("engine", ["pymupdf", "reportlab"])
def test_build_pdf_both_engines(tmp_path: Path, engine: str):
    pages = []
    img = _decode(make_image((800, 1200)), "a.jpg")
    pages.append((image_utils.encode_jpeg(img, 85), img.width, img.height))
    img2 = _decode(make_image((1200, 600)), "b.jpg")
    pages.append((image_utils.encode_jpeg(img2, 85), img2.width, img2.height))

    out = tmp_path / f"out-{engine}.pdf"
    count = pdf_builder.build_pdf(pages, out, engine=engine, margin_pt=0, title="测试")
    assert count == 2
    assert out.stat().st_size > 0

    with fitz.open(out) as doc:
        assert doc.page_count == 2
        assert doc.metadata["title"] == "测试"
        # 两个引擎都必须输出 A4 纵向页面
        for page in doc:
            assert abs(page.rect.width - pdf_builder.A4_WIDTH_PT) < 1
            assert abs(page.rect.height - pdf_builder.A4_HEIGHT_PT) < 1


def test_build_pdf_never_leaves_part_file(tmp_path: Path):
    out = tmp_path / "oops.pdf"
    with pytest.raises(Exception):
        pdf_builder.build_pdf([], out)
    assert not out.exists()
    assert not list(tmp_path.glob("*.part"))


def test_split_pages_for_long_image():
    img = _decode(make_image((1000, 5000)), "long.jpg")
    pages = image_utils.split_pages(img, "split", usable_ratio=pdf_builder.usable_ratio(0), quality=80)
    assert len(pages) > 1
    assert all(len(payload) > 0 for payload, _, _ in pages)


def test_fit_mode_keeps_single_page():
    img = _decode(make_image((1000, 5000)), "long.jpg")
    pages = image_utils.split_pages(img, "fit", usable_ratio=pdf_builder.usable_ratio(0), quality=80)
    assert len(pages) == 1


def test_convert_streams_to_pdf_end_to_end(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "static_dir", tmp_path)
    result = pdf_service.convert_streams_to_pdf(
        [("照片 1.jpg", make_image((900, 1200))), ("照片2.png", make_image((900, 1200), fmt="PNG"))],
        page_mode="fit",
    )
    assert result.page_count == 2
    assert result.source_count == 2
    assert result.path.parent == tmp_path / settings.pdf_subdir
    # 文件名被清洗：不含空格与中文，只保留日期 + 随机串
    assert " " not in result.file_name
    assert result.file_name.endswith(".pdf")


def test_safe_slug_strips_path_traversal():
    for raw in ["../../etc/passwd", "C:\\Windows\\evil.pdf", "/tmp/../../x.pdf", "我的 照片 (1).jpg"]:
        slug = storage.safe_slug(raw)
        assert ".." not in slug
        assert "/" not in slug and "\\" not in slug
        assert all(ch.isalnum() or ch in "_-" for ch in slug)

    assert storage.safe_slug(None) == ""
    assert storage.safe_slug("正常照片.jpg") == ""  # 纯中文会被清洗为空
    assert "photo" in storage.safe_slug("my photo.png")


def test_new_pdf_path_never_escapes_pdf_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "static_dir", tmp_path)
    path = storage.new_pdf_path(settings, prefix="../../etc/passwd")
    assert path.parent == tmp_path / settings.pdf_subdir
    assert path.name.endswith(".pdf")


def test_resolve_upload_path_rejects_bad_ids(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "var_dir", tmp_path)
    for bad in ["../../etc/passwd", "A" * 32, "zz" * 16, "", None, 123]:
        with pytest.raises(ApiError) as excinfo:
            storage.resolve_upload_path(settings, bad)  # type: ignore[arg-type]
        assert excinfo.value.code == "INVALID_IMAGE_ID"

    good = "0f" * 16
    assert storage.resolve_upload_path(settings, good) == settings.upload_dir / f"{good}.jpg"


def test_cleanup_uploads_removes_stale_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "var_dir", tmp_path)
    upload_dir = tmp_path / settings.upload_subdir
    upload_dir.mkdir(parents=True, exist_ok=True)

    stale = upload_dir / ("a" * 32 + ".jpg")
    kept = upload_dir / ("b" * 32 + ".jpg")
    for item in (stale, kept):
        item.write_bytes(b"jpegdata")

    now = time.time()
    old = now - (settings.upload_ttl_minutes + 5) * 60
    os.utime(stale, (old, old))

    removed = storage.cleanup_uploads(settings, now=now)
    assert removed == 1
    assert not stale.exists()
    assert kept.exists()


def test_save_temp_image_normalizes_and_is_reusable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "var_dir", tmp_path)
    image_id, path = pdf_service._save_temp_image_sync(
        make_image((300, 400), fmt="PNG"), "原图.png", settings
    )
    assert re.fullmatch(r"[0-9a-f]{32}", image_id)
    assert path.is_file() and path.suffix == ".jpg"
    # 暂存的是可直接合成的规范化 JPEG
    assert image_utils.sniff_format(path.read_bytes()) == "JPEG"


def test_cleanup_expired_removes_old_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "static_dir", tmp_path)
    target_dir = tmp_path / settings.pdf_subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    old = target_dir / "old.pdf"
    fresh = target_dir / "fresh.pdf"
    leftover = target_dir / "broken.pdf.part"
    for f in (old, fresh, leftover):
        f.write_bytes(b"%PDF-1.4")

    now = time.time()
    # 把 old 和残留文件的时间改到 3 小时前
    old_time = now - 3 * 3600
    os.utime(old, (old_time, old_time))
    os.utime(leftover, (old_time, old_time))

    removed = storage.cleanup_expired(settings, now=now)
    assert removed == 2
    assert not old.exists()
    assert not leftover.exists()
    assert fresh.exists()
