"""接口级测试：/api/v1/convert-to-pdf 与静态文件访问。"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from conftest import TMP_STATIC, files_payload, make_image

from app.config import settings
from app.services.pdf_builder import load_pymupdf

fitz = load_pymupdf()

CONVERT_URL = "/api/v1/convert-to-pdf"


def _pdf_path_from_url(url: str) -> Path:
    name = url.rsplit("/", 1)[-1]
    return Path(TMP_STATIC) / settings.pdf_subdir / name


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["pdf_engine"] in ("pymupdf", "reportlab")


def test_convert_multiple_images_keeps_order_and_pages(client):
    payload = files_payload(
        [
            (make_image((900, 1200), fmt="JPEG"), "image/jpeg"),
            (make_image((1200, 900), fmt="PNG"), "image/png"),
            (make_image((800, 800), fmt="JPEG"), "image/jpeg"),
        ]
    )
    resp = client.post(CONVERT_URL, files=payload)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["code"] == 0
    assert body["source_count"] == 3
    assert body["page_count"] == 3
    assert body["pdf_url"].endswith(".pdf")
    assert f"/static/{settings.pdf_subdir}/" in body["pdf_url"]
    assert body["size_bytes"] > 0

    # 文件真的落盘了，且页数与宣告一致
    pdf_path = _pdf_path_from_url(body["pdf_url"])
    assert pdf_path.is_file()

    with fitz.open(pdf_path) as doc:
        assert doc.page_count == 3
        for page in doc:
            # A4 纵向：595.28 x 841.89 pt（允许 1pt 误差）
            assert abs(page.rect.width - 595.2755905511812) < 1
            assert abs(page.rect.height - 841.8897637795277) < 1


def test_convert_with_single_file_field(client):
    """兼容微信小程序 wx.uploadFile 的单文件字段名 file。"""
    payload = files_payload([(make_image(), "image/jpeg")], field="file")
    resp = client.post(CONVERT_URL, files=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["page_count"] == 1


def test_generated_pdf_is_served_by_static_files(client):
    payload = files_payload([(make_image(), "image/jpeg")])
    url = client.post(CONVERT_URL, files=payload).json()["pdf_url"]

    static_path = "/" + url.split("/", 3)[-1]  # 去掉 scheme://host
    resp = client.get(static_path)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")


def test_no_files_returns_400(client):
    resp = client.post(CONVERT_URL)
    assert resp.status_code == 400
    assert resp.json()["code"] == "NO_FILES"


def test_too_many_files_returns_400(client, monkeypatch):
    monkeypatch.setattr(settings, "max_file_count", 2)
    payload = files_payload([(make_image((200, 200)), "image/jpeg")] * 3)
    resp = client.post(CONVERT_URL, files=payload)
    assert resp.status_code == 400
    assert resp.json()["code"] == "TOO_MANY_FILES"


def test_file_too_large_returns_413(client, monkeypatch):
    monkeypatch.setattr(settings, "max_file_size_mb", 0)
    payload = files_payload([(make_image((500, 500)), "image/jpeg")])
    resp = client.post(CONVERT_URL, files=payload)
    assert resp.status_code == 413
    assert resp.json()["code"] == "FILE_TOO_LARGE"


def test_non_image_is_rejected(client):
    """伪装成 image/jpeg 的文本文件必须被文件头识别拦下。"""
    payload = files_payload([(b"not an image at all" * 10, "image/jpeg")])
    resp = client.post(CONVERT_URL, files=payload)
    assert resp.status_code == 415
    assert resp.json()["code"] == "UNSUPPORTED_TYPE"


def test_corrupted_image_returns_400(client):
    broken = b"\xff\xd8\xff" + os.urandom(4096)  # 有 JPEG 头但没有有效数据
    payload = files_payload([(broken, "image/jpeg")])
    resp = client.post(CONVERT_URL, files=payload)
    assert resp.status_code == 400
    assert resp.json()["code"] in ("IMAGE_DECODE_FAILED", "UNSUPPORTED_TYPE")


def test_invalid_page_mode_returns_400(client):
    payload = files_payload([(make_image((300, 300)), "image/jpeg")])
    resp = client.post(CONVERT_URL, files=payload, data={"page_mode": "whatever"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_PARAM"


def test_split_mode_slices_long_screenshot(client):
    payload = files_payload([(make_image((1000, 6000)), "image/jpeg")])
    resp = client.post(CONVERT_URL, files=payload, data={"page_mode": "split"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_count"] >= 4  # 1000x6000 的长图应被切成多页

    with fitz.open(_pdf_path_from_url(body["pdf_url"])) as doc:
        assert doc.page_count == body["page_count"]


def test_public_base_url_is_used(client, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://cdn.example.com/")
    payload = files_payload([(make_image((300, 300)), "image/jpeg")])
    url = client.post(CONVERT_URL, files=payload).json()["pdf_url"]
    assert url.startswith(f"https://cdn.example.com/static/{settings.pdf_subdir}/")


def test_request_body_limit_rejects_before_parsing(client, monkeypatch):
    monkeypatch.setattr(settings, "max_request_body_mb", 0)
    payload = files_payload([(make_image((300, 300)), "image/jpeg")])
    resp = client.post(CONVERT_URL, files=payload)
    assert resp.status_code == 413
    assert resp.json()["code"] == "REQUEST_TOO_LARGE"


def test_cors_headers_are_returned(client):
    payload = files_payload([(make_image((300, 300)), "image/jpeg")])
    resp = client.post(CONVERT_URL, files=payload, headers={"Origin": "https://miniapp.example.com"})
    assert resp.headers.get("access-control-allow-origin") == "*"

    preflight = client.options(
        CONVERT_URL,
        headers={
            "Origin": "https://miniapp.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "*"


# ---------------------- base64（小程序友好）入口 ----------------------
BASE64_URL = "/api/v1/convert-to-pdf-base64"


def test_convert_base64_json(client):
    images = [
        {
            "file_name": f"photo-{idx}.jpg",
            "data": base64.b64encode(make_image((600, 900))).decode("ascii"),
        }
        for idx in range(2)
    ]
    resp = client.post(BASE64_URL, json={"images": images, "pdf_title": "scan"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_count"] == 2
    assert body["source_count"] == 2
    assert Path(TMP_STATIC, settings.pdf_subdir, body["file_name"]).is_file()


def test_convert_base64_accepts_data_url_prefix(client):
    encoded = base64.b64encode(make_image((400, 400), fmt="PNG")).decode("ascii")
    resp = client.post(BASE64_URL, json={"images": [{"data": f"data:image/png;base64,{encoded}"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["page_count"] == 1


def test_convert_base64_invalid_payload_returns_400(client):
    resp = client.post(BASE64_URL, json={"images": [{"data": "!!!not-base64!!!"}]})
    assert resp.status_code == 400
    assert resp.json()["code"] == "IMAGE_DECODE_FAILED"


def test_convert_base64_requires_at_least_one_image(client):
    resp = client.post(BASE64_URL, json={"images": []})
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_PARAM"


# --------------- 分张上传（wx.uploadFile 循环上传）入口 ---------------
UPLOAD_URL = "/api/v1/upload-image"
BY_IDS_URL = "/api/v1/convert-to-pdf-by-ids"


def test_upload_then_merge_by_ids(client):
    """小程序场景：循环 wx.uploadFile 拿 id，再用 id 数组一次合成。"""
    image_ids = []
    for index in range(3):
        resp = client.post(
            UPLOAD_URL,
            files={"file": (f"{index}.jpg", make_image((600, 800)), "image/jpeg")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert re.fullmatch(r"[0-9a-f]{32}", body["image_id"])
        assert body["size_bytes"] > 0
        image_ids.append(body["image_id"])

    # 暂存目录里应该有 3 张规范化后的原图
    staged = sorted(p.name for p in Path(settings.upload_dir).glob("*.jpg"))
    assert len(staged) == 3

    # 倒序提交，验证排版顺序由 image_ids 数组决定
    resp = client.post(
        BY_IDS_URL, json={"image_ids": list(reversed(image_ids)), "pdf_title": "分张上传"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_count"] == 3
    assert body["source_count"] == 3
    assert Path(TMP_STATIC, settings.pdf_subdir, body["file_name"]).is_file()

    # 合成成功后暂存图立即清理
    assert list(Path(settings.upload_dir).glob("*.jpg")) == []


def test_upload_image_accepts_field_alias(client):
    resp = client.post(
        UPLOAD_URL, files={"image": ("a.jpg", make_image((400, 400)), "image/jpeg")}
    )
    assert resp.status_code == 200, resp.text
    Path(settings.upload_dir, resp.json()["image_id"] + ".jpg").unlink(missing_ok=True)


def test_upload_image_rejects_non_image(client):
    resp = client.post(UPLOAD_URL, files={"file": ("x.jpg", b"i am not a picture", "image/jpeg")})
    assert resp.status_code == 415
    assert resp.json()["code"] == "UNSUPPORTED_TYPE"


def test_upload_image_without_file_returns_400(client):
    resp = client.post(UPLOAD_URL)
    assert resp.status_code == 400
    assert resp.json()["code"] == "NO_FILES"


def test_convert_by_ids_unknown_id_returns_404(client):
    resp = client.post(BY_IDS_URL, json={"image_ids": ["0" * 32]})
    assert resp.status_code == 404
    assert resp.json()["code"] == "IMAGE_NOT_FOUND"


def test_convert_by_ids_rejects_path_traversal(client):
    resp = client.post(BY_IDS_URL, json={"image_ids": ["../../etc/passwd"]})
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_IMAGE_ID"


def test_convert_by_ids_requires_ids(client):
    resp = client.post(BY_IDS_URL, json={"image_ids": []})
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_PARAM"


def test_staged_images_are_not_publicly_served(client):
    """暂存原图放在 var/ 而非 static/ 下，即使知道文件名也拿不到。"""
    resp = client.post(UPLOAD_URL, files={"file": ("p.jpg", make_image((400, 400)), "image/jpeg")})
    image_id = resp.json()["image_id"]

    for path in (f"/static/uploads/{image_id}.jpg", f"/static/{image_id}.jpg"):
        assert client.get(path).status_code == 404

    Path(settings.upload_dir, image_id + ".jpg").unlink(missing_ok=True)
