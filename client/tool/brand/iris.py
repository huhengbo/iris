"""生成「光圈」的图标、启动屏图标和 TV 横幅（见 DESIGN.md「品牌」一节）。

图形只有一份几何定义：100 网格里半径 30 的深色圆盘，中间六边形开口，六条叶片缝。
矢量资源直接写 Android VectorDrawable；位图用本机 Chrome 无头模式从 SVG 渲染。

运行（在 client/ 下）：uv run --with shapely python tool/brand/iris.py
"""

import math
import os
import subprocess
import tempfile
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

CLIENT = Path(__file__).resolve().parents[2]
RES = CLIENT / "android/app/src/main/res"
WEB = CLIENT / "web"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

AMBER = "#E9A548"
INK = "#1E1408"
BG = "#120E0B"

DISK = 30.0  # 圆盘半径
HOLE = 11.0  # 六边形开口外接圆半径，顶点朝上
GAP = 2.4  # 叶片缝宽


def blades() -> list[list[tuple[float, float]]]:
    """六片叶片的外轮廓，坐标以圆心为原点。"""
    hexagon = [
        (HOLE * math.cos(math.radians(-90 + 60 * k)), HOLE * math.sin(math.radians(-90 + 60 * k)))
        for k in range(6)
    ]
    cuts = []
    for k in range(6):
        a, b = hexagon[k], hexagon[(k + 1) % 6]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        # 沿六边形的边延长到圆外，缝从开口一直切到边缘
        end = (a[0] + dx / length * (DISK + 4) * 2, a[1] + dy / length * (DISK + 4) * 2)
        cuts.append(LineString([a, end]).buffer(GAP / 2, cap_style="flat"))
    shape = Point(0, 0).buffer(DISK, quad_segs=64).difference(Polygon(hexagon)).difference(unary_union(cuts))
    return [list(p.exterior.coords)[:-1] for p in shape.geoms]


def path_data(polys, cx: float, cy: float, k: float) -> str:
    """多边形转成路径；落在圆周上的连续顶点合并成圆弧，文件小、放大不出棱角。"""
    out = []
    r = DISK * k
    for poly in polys:
        n = len(poly)
        on_arc = [abs(math.hypot(x, y) - DISK) < 1e-6 for x, y in poly]
        # 从一个不在圆周上的点起笔，弧段不会被起点截断
        start = next(i for i in range(n) if not on_arc[i])
        pts = [poly[(start + i) % n] for i in range(n)] + [poly[start]]
        arc = [on_arc[(start + i) % n] for i in range(n)] + [False]
        fmt = lambda p: f"{cx + p[0] * k:.2f},{cy + p[1] * k:.2f}"  # noqa: E731
        cmd = [f"M{fmt(pts[0])}"]
        i = 1
        while i < len(pts):
            if arc[i]:
                j = i
                while arc[j + 1]:
                    j += 1
                cmd.append(f"L{fmt(pts[i])}")
                if j > i:
                    # 角度增大即屏幕坐标里的顺时针，对应 sweep=1；每片叶片的弧都小于半圆
                    turn = math.atan2(pts[i + 1][1], pts[i + 1][0]) - math.atan2(pts[i][1], pts[i][0])
                    sweep = 1 if math.sin(turn) > 0 else 0
                    cmd.append(f"A{r:.2f},{r:.2f} 0 0,{sweep} {fmt(pts[j])}")
                i = j + 1
            else:
                cmd.append(f"L{fmt(pts[i])}")
                i += 1
        out.append("".join(cmd) + "Z")
    return "".join(out)


def vector(name: str, size: int, paths: list[tuple[str, str]], comment: str) -> None:
    body = "\n".join(f'    <path android:fillColor="{c}" android:pathData="{d}" />' for c, d in paths)
    (RES / "drawable" / f"{name}.xml").write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<!-- {comment}由 tool/brand/iris.py 生成，不要手改 -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="{size}dp"
    android:height="{size}dp"
    android:viewportWidth="{size}"
    android:viewportHeight="{size}">
{body}
</vector>
""",
        encoding="utf-8",
    )


def circle(cx: float, cy: float, r: float) -> str:
    return f"M{cx - r:.2f},{cy:.2f}a{r:.2f},{r:.2f} 0 1,0 {2 * r:.2f},0a{r:.2f},{r:.2f} 0 1,0 {-2 * r:.2f},0Z"


def svg_icon(size: int, k: float, radius: float) -> str:
    """方形位图图标：琥珀底（radius 为圆角占边长的比例），叶片按 k 缩放。"""
    c = size / 2
    d = path_data(BLADES, c, c, k * size / 100)
    rx = radius * size
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<rect width="{size}" height="{size}" rx="{rx}" fill="{AMBER}"/><path d="{d}" fill="{INK}"/></svg>'
    )


def render(html: str, width: int, height: int, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(
            f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0;background:transparent;overflow:hidden}}</style>{html}',
            encoding="utf-8",
        )
        subprocess.run(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                "--virtual-time-budget=5000",
                f"--window-size={width},{height}",
                f"--screenshot={out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    print("wrote", out.relative_to(CLIENT))


BLADES = blades()


def main() -> None:
    # 自适应图标：108dp 画布里可见约 72dp，圆盘直径取可见部分的 60%
    adaptive = path_data(BLADES, 54, 54, 0.72)
    vector("ic_launcher_foreground", 108, [(INK, adaptive)], "桌面图标前景，背景是琥珀色。")
    vector("ic_launcher_monochrome", 108, [("#FF000000", adaptive)], "主题图标，系统只取形状再着色。")
    # Android 12+ 启动屏：240dp 画布，系统画 160dp 的琥珀圆底；Flutter 开机动画从同一位置接着画
    vector("splash_icon", 240, [(INK, path_data(BLADES, 120, 120, 1.6))], "Android 12+ 系统启动屏图标。")
    # Android 12 以下的启动屏没有图标底，自己画琥珀圆
    vector(
        "launch_icon",
        240,
        [(AMBER, circle(120, 120, 80)), (INK, path_data(BLADES, 120, 120, 1.6))],
        "Android 12 以下的启动屏图标。",
    )
    print("wrote android vectors")
    # 网页开机画面，和 Android 启动屏同尺寸；Flutter 第一帧接着画
    (WEB / "icons/splash.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240">'
        f'<path d="{circle(120, 120, 80)}" fill="{AMBER}"/><path d="{path_data(BLADES, 120, 120, 1.6)}" fill="{INK}"/></svg>\n',
        encoding="utf-8",
    )
    print("wrote web/icons/splash.svg")

    # Android 7.x 没有自适应图标，用圆角方块位图
    for folder, size in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
        render(svg_icon(size, 0.92, 0.22), size, size, RES / f"mipmap-{folder}" / "ic_launcher.png")
    # 网页：普通图标带圆角；maskable 铺满，内容缩进安全区
    render(svg_icon(192, 1, 0.22), 192, 192, WEB / "icons/Icon-192.png")
    render(svg_icon(512, 1, 0.22), 512, 512, WEB / "icons/Icon-512.png")
    render(svg_icon(192, 0.72 * 100 / 108, 0), 192, 192, WEB / "icons/Icon-maskable-192.png")
    render(svg_icon(512, 0.72 * 100 / 108, 0), 512, 512, WEB / "icons/Icon-maskable-512.png")
    render(svg_icon(32, 1.1, 0.22), 32, 32, WEB / "favicon.png")

    # Android TV 横幅 320×180：图标 + 中英文名
    icon = (
        f'<svg width="112" height="112" viewBox="0 0 112 112"><circle cx="56" cy="56" r="56" fill="{AMBER}"/>'
        f'<path d="{path_data(BLADES, 56, 56, 1.12)}" fill="{INK}"/></svg>'
    )
    render(
        f'<div style="width:320px;height:180px;background:{BG};display:flex;align-items:center;justify-content:center;gap:24px">'
        f"{icon}"
        '<div style="display:grid;line-height:1.1;font-family:-apple-system,\'PingFang SC\',sans-serif">'
        '<span style="color:rgba(255,246,234,.94);font-size:40px;font-weight:700">光圈</span>'
        f'<span style="color:{AMBER};font-size:30px;font-weight:600">iris</span></div></div>',
        320,
        180,
        RES / "drawable-xhdpi/tv_banner.png",
    )


if __name__ == "__main__":
    main()
