"""测试夹具：把静态目录指向临时目录，避免污染仓库。"""

from __future__ import annotations

import os
import shutil
import tempfile

# 必须在导入 main 之前设置，因为 StaticFiles 在挂载时会读取配置
TMP_STATIC = tempfile.mkdtemp(prefix="aipdf-static-")
TMP_VAR = tempfile.mkdtemp(prefix="aipdf-var-")
os.environ["STATIC_DIR"] = TMP_STATIC
os.environ["VAR_DIR"] = TMP_VAR
os.environ["PDF_TTL_HOURS"] = "1"
os.environ["UPLOAD_TTL_MINUTES"] = "30"
os.environ["CLEANUP_INTERVAL_MINUTES"] = "60"
os.environ["PDF_ENGINE"] = "auto"
os.environ["PAGE_MODE"] = "fit"

from io import BytesIO  # noqa: E402
from typing import List, Tuple  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from main import app

    with TestClient(app) as test_client:
        yield test_client
    shutil.rmtree(TMP_STATIC, ignore_errors=True)
    shutil.rmtree(TMP_VAR, ignore_errors=True)


@pytest.fixture
def static_dir() -> str:
    return TMP_STATIC


def make_image(
    size: Tuple[int, int] = (900, 1200),
    *,
    fmt: str = "JPEG",
    color=(210, 60, 60),
) -> bytes:
    """生成一张带图形内容的测试图片（RGBA 请使用 PNG 格式）。"""
    mode = "RGBA" if fmt == "PNG" else "RGB"
    image = Image.new(mode, size, (255, 255, 255, 0) if mode == "RGBA" else (250, 250, 250))
    draw = ImageDraw.Draw(image)
    draw.rectangle([size[0] * 0.1, size[1] * 0.1, size[0] * 0.9, size[1] * 0.4], fill=color)
    draw.ellipse([size[0] * 0.2, size[1] * 0.5, size[0] * 0.8, size[1] * 0.9], outline=(20, 20, 20), width=8)
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def files_payload(items: List[Tuple[str, bytes, str]], field: str = "files"):
    """构造 multipart 文件列表。"""
    suffixes = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "text/plain": "txt"}
    return [
        (field, (f"{field}-{idx}.{suffixes.get(ctype, 'bin')}", data, ctype))
        for idx, (data, ctype) in enumerate(items)
    ]
