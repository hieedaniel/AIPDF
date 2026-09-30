"""自检模块测试：确保「能让功能静默失效」的配置问题一定会出现在 /health 的 warnings 里。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import Settings
from app.core import diagnostics


def _cfg(**overrides) -> Settings:
    base = {
        "public_base_url": "https://aipdf.seveninfo.cn",
        "static_dir": Path("/tmp/aipdf-test-static"),
        "var_dir": Path("/tmp/aipdf-test-var"),
        "pdf_engine": "pymupdf",  # 跳过引擎探测，保持测试与运行环境无关
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url",
    [
        "https://yourdomain.com",
        "https://yourdomain.com/",
        "http://pdf.yourdomain.com",
        "https://example.com",
    ],
)
def test_placeholder_domain_is_reported(url):
    """占位域名必须被点出来：这是「合成成功但客户端下载失败」的头号原因。"""
    warnings = diagnostics.check_public_base_url(_cfg(public_base_url=url))
    assert len(warnings) == 1
    assert "占位" in warnings[0]
    assert "PUBLIC_BASE_URL" in warnings[0]


def test_missing_public_base_url_is_reported():
    warnings = diagnostics.check_public_base_url(_cfg(public_base_url=None))
    assert len(warnings) == 1
    assert "未配置 PUBLIC_BASE_URL" in warnings[0]


def test_http_public_base_url_is_reported():
    warnings = diagnostics.check_public_base_url(_cfg(public_base_url="http://aipdf.seveninfo.cn"))
    assert len(warnings) == 1
    assert "https" in warnings[0].lower()


def test_correct_public_base_url_has_no_warning():
    assert diagnostics.check_public_base_url(_cfg()) == []


def test_real_domain_containing_example_is_not_flagged():
    """`myexample.com` 不是占位域名，不能误伤。"""
    assert diagnostics.check_public_base_url(_cfg(public_base_url="https://myexample.com")) == []


def test_writable_dirs_have_no_warning(tmp_path):
    cfg = _cfg(static_dir=tmp_path / "static", var_dir=tmp_path / "var")
    assert diagnostics.check_dirs(cfg) == []
    # 探针文件必须被清掉，不能留在静态目录里被对外访问
    assert not list((tmp_path / "static").glob(".write_probe"))


def test_unwritable_dir_is_reported(tmp_path):
    target = tmp_path / "static"
    target.mkdir()
    target.chmod(0o500)  # r-x：不可写
    try:
        # root 无视权限位，CI 以 root 跑时跳过
        if os.access(target, os.W_OK):
            pytest.skip("当前用户对目录仍有写权限（例如 root），无法模拟不可写")
        cfg = _cfg(static_dir=target, var_dir=target)
        warnings = diagnostics.check_dirs(cfg)
        assert len(warnings) == 1
        assert "STORAGE_NOT_WRITABLE" in warnings[0]
        assert "chown" in warnings[0]
    finally:
        target.chmod(0o700)


def test_collect_caches_until_reset(tmp_path):
    cfg = _cfg(public_base_url=None, static_dir=tmp_path, var_dir=tmp_path)
    try:
        first = diagnostics.collect(force=True, cfg=cfg)
        assert any("未配置 PUBLIC_BASE_URL" in w for w in first)
        # 未 force 时命中缓存
        assert diagnostics.collect() == first
    finally:
        diagnostics.reset_cache()
