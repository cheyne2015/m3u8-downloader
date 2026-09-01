"""m3u8-downloader 安装配置."""
from setuptools import setup, find_packages

setup(
    name="m3u8-downloader",
    version="1.2.0",
    description="本地 m3u8 下载工具，支持 TS 片段并发下载与 MP4 转换",
    author="m3u8-downloader",
    python_requires=">=3.8",
    packages=find_packages(),
    install_requires=[
        "requests>=2.28.0",
        "pycryptodome>=3.18.0",
        "tqdm>=4.65.0",
        "beautifulsoup4>=4.12.0",
    ],
    extras_require={
        # 深度模式（无头浏览器抽取）可选依赖：
        #   pip install "m3u8-downloader[deep]" && playwright install chromium
        "deep": ["playwright>=1.40.0"],
    },
    entry_points={
        "console_scripts": [
            "m3u8-dl=m3u8_downloader.cli:main",
            "m3u8-dl-gui=m3u8_downloader.gui:run_gui",
        ],
    },
)
