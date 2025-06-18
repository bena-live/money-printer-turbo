"""
MoviePy logger configuration to suppress unwanted output
"""
import os
import sys
import logging
from contextlib import contextmanager

# 设置环境变量来禁止 MoviePy 的进度条
os.environ["MOVIEPY_FFMPEG_NO_BAR"] = "1"

# 配置 imageio 的日志级别
logging.getLogger("imageio").setLevel(logging.ERROR)
logging.getLogger("imageio_ffmpeg").setLevel(logging.ERROR)

# 配置 MoviePy 的日志级别
logging.getLogger("moviepy").setLevel(logging.ERROR)


@contextmanager
def suppress_moviepy_output():
    """
    Context manager to suppress MoviePy's stdout/stderr output
    """
    # 保存原始的 stdout 和 stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    
    # 创建一个 null 设备来丢弃输出
    class NullDevice:
        def write(self, s):
            pass
        def flush(self):
            pass
    
    try:
        # 重定向输出到 null 设备
        sys.stdout = NullDevice()
        sys.stderr = NullDevice()
        yield
    finally:
        # 恢复原始输出
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def init_moviepy_logger():
    """
    初始化 MoviePy 相关的日志配置
    """
    # 禁用 MoviePy 的自动检测输出
    import moviepy.config as moviepy_config
    moviepy_config.check_config = lambda: None
    
    # 设置 ffmpeg 的日志级别
    os.environ["FFMPEG_LOG_LEVEL"] = "error"
    
    # 禁用 imageio 的下载进度输出
    os.environ["IMAGEIO_NO_INTERNET"] = "1"