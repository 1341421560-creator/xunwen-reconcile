import os
import time


_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def replace_snapshot(stage, destination):
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            os.replace(stage, destination)
            return
        except PermissionError as exc:
            # Windows 后台程序可能短暂占用文件。始终持有账本锁，只重试原子替换。
            # 真正的权限故障在有限次数后返回，禁止退回原地覆盖或清空正式文件。
            if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == len(_RETRY_DELAYS):
                raise
            time.sleep(_RETRY_DELAYS[attempt])
