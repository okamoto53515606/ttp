"""アイコン生成スクリプト

resources/ttp.svg と同じ図柄（ひし形 + 白い t）を Pillow で描画し、
Windows 用の resources/ttp.ico と、Tkinter 用の resources/ttp.png を生成する。

使い方: uv run python scripts/make_icon.py
色や文字を変えたいときは FILL / LETTER を編集して再実行する。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
RES_DIR = ROOT / "resources"

# ── 見た目の設定 ──────────────────────────────────────────
FILL = "#0f766e"  # ひし形の色（元サイトは #001f3f）
LETTER = "t"  # 中央の文字
FONT_SIZE = 68  # viewBox 100x100 基準のフォントサイズ
SUPERSAMPLE = 4  # 拡大描画して縮小 → アンチエイリアス
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
PNG_SIZE = 256


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """太字フォントを読み込む（見つからなければ既定フォント）。"""
    for name in ("arialbd.ttf", "segoeuib.ttf", "Arial Bold"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(size: int) -> Image.Image:
    """1 サイズ分のアイコンを描画する。"""
    s = size * SUPERSAMPLE
    scale = s / 100  # ttp.svg の viewBox (100x100) を基準にする

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ひし形（元サイトと同じ比率）
    draw.polygon(
        [
            (50 * scale, 5 * scale),
            (95 * scale, 50 * scale),
            (50 * scale, 95 * scale),
            (5 * scale, 50 * scale),
        ],
        fill=FILL,
    )

    # 文字は実測 bbox で中央合わせ（ベースライン基準だとずれるため）
    font = _load_font(int(FONT_SIZE * scale))
    left, top, right, bottom = draw.textbbox((0, 0), LETTER, font=font)
    x = s / 2 - (left + right) / 2
    y = s / 2 - (top + bottom) / 2
    draw.text((x, y), LETTER, font=font, fill="#ffffff")

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    RES_DIR.mkdir(exist_ok=True)

    ico_path = RES_DIR / "ttp.ico"
    render(max(SIZES)).save(ico_path, format="ICO", sizes=[(s, s) for s in SIZES])

    png_path = RES_DIR / "ttp.png"
    render(PNG_SIZE).save(png_path, format="PNG")

    print(f"生成: {ico_path} ({', '.join(f'{s}x{s}' for s in SIZES)})")
    print(f"生成: {png_path} ({PNG_SIZE}x{PNG_SIZE})")


if __name__ == "__main__":
    main()
