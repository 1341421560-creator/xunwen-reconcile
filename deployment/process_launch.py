import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def launch_service(root, port):
    root = Path(root).resolve()
    directory = root / "temp"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]
    # 显式绑定全部标准句柄，后台服务不会持有启动窗口或调用者的管道。
    with (directory / ("server-" + stamp + ".log")).open("xb") as output, (directory / ("server-" + stamp + ".error.log")).open("xb") as error:
        process = subprocess.Popen([sys.executable, "-B", "-X", "utf8", str(root / "scripts" / "entry.py"), "server", "--port", str(port)],
                                   cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=error, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    return {"pid": process.pid}
