"""PyInstaller ビルドスクリプト

ttp.exe を生成する。
使い方: uv run python build.py
"""

import PyInstaller.__main__
import shutil
import zipfile
from pathlib import Path

from ttp import __version__


def build() -> None:
    dist_dir = Path("dist")
    build_dir = Path("build")

    # クリーンアップ
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)

    PyInstaller.__main__.run(
        [
            "src/ttp/__main__.py",
            "--name=ttp",
            "--noconsole",
            "--add-data=resources;resources",
            "--noconfirm",
            "--clean",
        ]
    )

    out = dist_dir / "ttp"

    # LICENSE を配布フォルダにコピー
    shutil.copy2("LICENSE", out / "LICENSE")

    # ログフォルダを準備 (空フォルダ + .gitkeep)
    logs_dir = out / "logs"
    logs_dir.mkdir(exist_ok=True)

    # バージョン付き zip を生成
    zip_name = f"ttp-{__version__}.zip"
    zip_path = dist_dir / zip_name
    print(f"\nzip 作成中: {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in out.rglob("*"):
            arcname = f"ttp/{file.relative_to(out)}"
            zf.write(file, arcname)
        # logs/ は空フォルダなので明示的にディレクトリエントリを追加
        zf.mkdir("ttp/logs/")

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)

    print()
    print("=" * 50)
    print(f"ビルド完了! (v{__version__})")
    print(f"出力先: {out}")
    print(f"配布zip: {zip_path} ({zip_size_mb:.1f} MB)")
    print()
    print("GitHub Release にアップロード:")
    print(f"  gh release create v{__version__} {zip_path} --title \"v{__version__}\" --notes \"TTP v{__version__}\"")
    print("=" * 50)


if __name__ == "__main__":
    build()
