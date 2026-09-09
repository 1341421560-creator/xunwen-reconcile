import argparse
from pathlib import Path
from deployment.test_runner import run_tests


def main():
    parser = argparse.ArgumentParser(description="使用离线环境运行测试，结果保存在 temp")
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args()
    raise SystemExit(run_tests(Path(__file__).resolve().parents[1], args.pattern))


if __name__ == "__main__":
    main()
