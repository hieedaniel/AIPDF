"""环境自检脚本 —— 供 start_backend.bat 调用，输出 KEY=VALUE 行。

单独放成文件而不是塞进 .bat 的 for /f 单行命令里，
是因为批处理对嵌套引号（尤其是单引号内的 Python 字符串）几乎无法正确转义。

用法：
    python scripts/check_env.py
输出：
    PYVER=3.13.12
    ENGINE=pymupdf
    DEPS=ok
"""

from __future__ import annotations

import importlib.util
import sys


def has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def main() -> int:
    # Python 版本
    print(f"PYVER={sys.version.split()[0]}")

    # PDF 引擎：PyMuPDF 优先，ReportLab 兜底
    if has("pymupdf") or has("fitz"):
        engine = "pymupdf"
    elif has("reportlab"):
        engine = "reportlab"
    else:
        engine = "none"
    print(f"ENGINE={engine}")

    # 必需的 Web 依赖
    required = ("fastapi", "uvicorn", "PIL", "pydantic_settings")
    missing = [name for name in required if not has(name)]
    print(f"DEPS={'ok' if not missing else 'missing'}")
    if missing:
        print(f"MISSING={','.join(missing)}")
        return 1
    if engine == "none":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
