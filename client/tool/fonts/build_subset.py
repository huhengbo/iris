"""从完整的 Noto Sans CJK SC 生成随客户端打包的中文子集。

    uv run --with fonttools --with brotli client/tool/fonts/build_subset.py

字符集 = ASCII/拉丁/标点/假名/全角 + GB2312 全部汉字 + Big5 常用字（繁体片名）
         + extra-chars.txt（常见用字里 GB2312 和 Big5 都没有的，如「嬛」「玥」「喆」）。
字体里没有的字符（韩文、泰文等）本来就不在 Noto Sans CJK SC 中，不受影响。
输出文件名带版本号（响应用一年 immutable 缓存）：改了字符集就递增版本，并同步 backend/app/web.py 的 WEB_FONT_NAME 和 pubspec.yaml。
"""

from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

VERSION = "v2"
HERE = Path(__file__).resolve().parent
SOURCE = HERE / "NotoSansCJKsc-Regular.otf"
TARGET = HERE.parent.parent / "assets" / "fonts" / f"NotoSansCJKsc-Regular.{VERSION}.otf"


def codec_chars(encoding: str, hi_range: range, lo_range: range) -> set[str]:
    chars = set()
    for hi in hi_range:
        for lo in lo_range:
            try:
                chars.add(bytes([hi, lo]).decode(encoding))
            except UnicodeDecodeError:
                pass
    return chars


def wanted_chars() -> set[str]:
    chars = {chr(c) for r in (range(0x20, 0x7F), range(0xA0, 0x250), range(0x1E00, 0x1F00), range(0x2000, 0x2070),
                              range(0x3000, 0x3100), range(0xFF00, 0xFFF0)) for c in r}
    chars |= set("→←↑↓★☆♪·•…—‘’“”")
    chars |= codec_chars("gb2312", range(0xA1, 0xF8), range(0xA1, 0xFF))
    chars |= codec_chars("big5", range(0xA4, 0xC7), range(0x40, 0xFF))  # 常用字 5401 个
    chars |= set((HERE / "extra-chars.txt").read_text(encoding="utf-8").split())
    return chars


def main() -> None:
    options = subset.Options()
    options.layout_features = ["kern"]  # 去掉竖排和字形变体，体积减半；界面只用横排
    options.name_IDs = [1, 2, 3, 4, 5, 6]
    options.hinting = False
    options.desubroutinize = True
    font = TTFont(SOURCE)
    subsetter = subset.Subsetter(options)
    subsetter.populate(text="".join(sorted(wanted_chars())))
    subsetter.subset(font)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    font.save(TARGET)
    print(f"{TARGET.name}: {TARGET.stat().st_size / 1e6:.2f} MB, {len(font.getGlyphOrder())} glyphs")


if __name__ == "__main__":
    main()
