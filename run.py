import argparse
from pathlib import Path
from reconcile.config import load_config
from reconcile.company_registry import CompanyRegistry
from reconcile.http_server import make_server


def main():
    root = Path(__file__).resolve().parent
    config = load_config(root)
    parser = argparse.ArgumentParser(description="循文对账本机服务")
    parser.add_argument("--port", type=int, default=config["port"])
    args = parser.parse_args()
    service = CompanyRegistry(root, config)
    server = make_server(service, config["host"], args.port)
    print(f"循文对账已启动：http://{config['host']}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
