"""沙箱后端：接口 + 离线假实现 + Docker 加固实现。"""

from .fake_backend import FakeSandbox
from .interface import ExecResult, Limits, SandboxBackend

__all__ = ["ExecResult", "Limits", "SandboxBackend", "FakeSandbox"]

# DockerSandbox 需要 docker 守护进程，按需导入：
#   from app.sandbox.docker_backend import DockerSandbox
