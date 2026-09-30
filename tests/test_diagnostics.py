"""自检模块测试：确保「能让功能静默失效」的配置问题一定会出现在 /health 的 warnings 里。"""

from __future__ import annotations

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


def test_unwritable_dir_is_reported(tmp_path, monkeypatch):
    """目录不可写必须被点名，并给出可操作的 chown 提示。

    不依赖 POSIX 权限位：它在 Windows 上无效、在 root 下也无效（root 无视权限位），
    所以直接让探针写入失败，两个平台行为一致。
    """
    target = tmp_path / "static"
    target.mkdir()
    var = tmp_path / "var"  # 保持可写，确保只报 STATIC_DIR 一条
    var.mkdir()

    real_write_bytes = Path.write_bytes

    def fake_write_bytes(self, data):
        if self.name == ".write_probe" and self.parent == target:
            raise PermissionError(13, "Permission denied")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", fake_write_bytes)

    warnings = diagnostics.check_dirs(_cfg(static_dir=target, var_dir=var))
    assert len(warnings) == 1
    assert warnings[0].startswith("STATIC_DIR=")
    assert "STORAGE_NOT_WRITABLE" in warnings[0]
    assert "chown" in warnings[0]


def test_dir_writable_uses_real_write_probe(tmp_path, monkeypatch):
    """只读权限位不够：必须真的写一次（bind mount 场景权限位毫无意义）。"""
    wrote: list[Path] = []
    real_write_bytes = Path.write_bytes

    def spy_write_bytes(self, data):
        wrote.append(self)
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", spy_write_bytes)
    assert diagnostics.check_dirs(_cfg(static_dir=tmp_path / "s", var_dir=tmp_path / "v")) == []
    assert [p.name for p in wrote] == [".write_probe", ".write_probe"]


def test_collect_caches_until_reset(tmp_path):
    cfg = _cfg(public_base_url=None, static_dir=tmp_path, var_dir=tmp_path)
    try:
        first = diagnostics.collect(force=True, cfg=cfg)
        assert any("未配置 PUBLIC_BASE_URL" in w for w in first)
        # 未 force 时命中缓存
        assert diagnostics.collect() == first
    finally:
        diagnostics.reset_cache()
