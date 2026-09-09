import argparse
import json
from pathlib import Path
from deployment.process_launch import launch_service


def main():
    parser = argparse.ArgumentParser(description="启动不依赖终端窗口的本机服务进程")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(launch_service(Path(__file__).resolve().parents[1], args.port)))


if __name__ == "__main__":
    main()
