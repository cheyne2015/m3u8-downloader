"""生成应用多尺寸 ICO 和数字输入框箭头资源。"""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "m3u8_downloader" / "assets"


def _app_icon() -> Image.Image:
    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(size):
        ratio = y / (size - 1)
        color = (
            int(32 + 12 * ratio),
            int(132 - 35 * ratio),
            int(255 - 18 * ratio),
            255,
        )
        for x in range(size):
            pixels[x, y] = color
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((28, 28, 996, 996), radius=220, fill=255)
    image.putalpha(mask)
    draw = ImageDraw.Draw(image)

    # 网页窗口：顶部圆点和内容区域在 16 像素图标中仍能形成明确轮廓。
    draw.rounded_rectangle((190, 170, 834, 690), radius=72, outline="white", width=54)
    draw.line((215, 300, 809, 300), fill="white", width=44)
    for x in (260, 330, 400):
        draw.ellipse((x - 20, 215, x + 20, 255), fill="white")

    # 播放符号表示网页视频，向下箭头表示从网页中提取。
    draw.polygon(((420, 370), (420, 570), (590, 470)), fill="white")
    draw.rounded_rectangle((458, 570, 566, 790), radius=40, fill="white")
    draw.polygon(((350, 735), (674, 735), (512, 900)), fill="white")
    return image


def _spin_arrow(name: str, color: tuple[int, int, int, int], upward: bool) -> None:
    scale = 4
    image = Image.new("RGBA", (12 * scale, 8 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    points = ((2, 6), (6, 2), (10, 6)) if upward else ((2, 2), (6, 6), (10, 2))
    draw.line(tuple((x * scale, y * scale) for x, y in points), fill=color, width=2 * scale, joint="curve")
    image.resize((12, 8), Image.Resampling.LANCZOS).save(ASSETS / name)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = _app_icon()
    icon.save(
        ASSETS / "m3u8-downloader.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(ASSETS / "m3u8-downloader.png")
    _spin_arrow("spin-up-dark.png", (232, 235, 239, 255), True)
    _spin_arrow("spin-down-dark.png", (232, 235, 239, 255), False)
    _spin_arrow("spin-up-light.png", (47, 57, 68, 255), True)
    _spin_arrow("spin-down-light.png", (47, 57, 68, 255), False)


if __name__ == "__main__":
    main()
