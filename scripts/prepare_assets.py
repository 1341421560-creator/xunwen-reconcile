from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    entries = [("启动对账软件.cmd", "start.ps1", False), ("停止对账软件.cmd", "stop.ps1", True),
               ("检查运行环境.cmd", "check.ps1", True), ("运行全部测试.cmd", "test.ps1", True),
               ("备份账本数据.cmd", "backup.ps1", True), ("恢复账本数据.cmd", "restore.ps1", True)]
    for name, script, pause in entries:
        content = ('@echo off\nsetlocal\n"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
                   '-NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\\' + script + '" %*\n'
                   'set result=%errorlevel%\n' + ('pause\n' if pause else 'if not %result%==0 pause\n') + 'exit /b %result%\n')
        (root / name).write_text(content, encoding="ascii", newline="\r\n")
    destination = root / "docs" / "licenses"
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(root / "downloads" / "python-3.12.10-embed-amd64.zip") as archive:
        (destination / "Python-LICENSE.txt").write_bytes(archive.read("LICENSE.txt"))
    for wheel in (root / "downloads").glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            names = [n for n in archive.namelist() if any(word in n.upper() for word in ("/LICENSE", "/LICENCE"))]
            names.sort(key=lambda n: n.endswith(".python"))
            for index, name in enumerate(names):
                suffix = "LICENSE.txt" if index == 0 else Path(name).name + ".txt"
                (destination / (wheel.name.split("-")[0] + "-" + suffix)).write_bytes(archive.read(name))
    # Windows PowerShell 5.1 需要 BOM 才能可靠识别含中文的 UTF-8 脚本。
    for script in list((root / "scripts").glob("*.ps1")) + list((root / "scripts").glob("*.psm1")):
        script.write_text(script.read_text(encoding="utf-8-sig"), encoding="utf-8-sig", newline="\n")
    print("启动入口和原始许可证已准备完成")


if __name__ == "__main__":
    main()
