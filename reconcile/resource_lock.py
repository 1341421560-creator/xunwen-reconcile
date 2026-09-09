import ctypes
import hashlib
import os
from contextlib import contextmanager
from threading import Event, Thread


@contextmanager
def resource_mutex(path):
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    canonical = os.path.normcase(str(path.resolve()))
    name = "Local\\XunwenLedger-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    ready, release = Event(), Event()
    state = {}

    def own_lock():
        handle = kernel.CreateMutexW(None, False, name)
        acquired = False
        try:
            if not handle:
                raise OSError("无法建立账本写入锁")
            acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
            if not acquired:
                raise ValueError("另一操作正在保存同一账本，请刷新后重试")
            ready.set()
            release.wait()
        except Exception as exc:
            state["error"] = exc
        finally:
            ready.set()
            if acquired:
                kernel.ReleaseMutex(handle)
            if handle:
                kernel.CloseHandle(handle)

    # 每次由独立线程持有内核锁，防止同一调用线程借助互斥体可重入特性绕过检查。
    # 进程意外退出时 Windows 自动释放锁，不依赖删除锁文件进行恢复。
    owner = Thread(target=own_lock, daemon=True)
    owner.start()
    ready.wait()
    if "error" in state:
        owner.join()
        raise state["error"]
    try:
        yield
    finally:
        release.set()
        owner.join()
