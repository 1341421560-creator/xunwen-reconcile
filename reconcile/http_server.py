import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs
from .input_validation import json_object
from .ledger_validation import LedgerCorruptionError
from .request_router import RequestRouter


def make_server(service, host, port):
    router = RequestRouter(service)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(service.config.get("request_timeout_seconds", 15))

        def trusted(self):
            expected = self.headers.get("Host", "")
            if expected not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                return False
            origin = self.headers.get("Origin")
            return not origin or origin in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")

        def respond(self, value, code=200):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                # 客户端离开不撤销已保存事务；重试仍受版本号约束。
                pass

        def do_GET(self):
            try:
                return self.get_request()
            except LedgerCorruptionError as exc:
                return self.respond({"error": str(exc)}, 503)
            except (ValueError, TypeError) as exc:
                return self.respond({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except Exception as exc:
                self.log_error("%s", str(exc))
                return self.respond({"error": "本机文件读取失败，请检查账本及目录访问权限"}, 500)

        def get_request(self):
            if not self.trusted():
                return self.respond({"error": "仅允许本机同源访问"}, 403)
            route = unquote(urlsplit(self.path).path)
            if "\x00" in route:
                raise ValueError("请求路径无效")
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("查询参数不能重复")
            payload = {key: values[0] for key, values in query.items()}
            if "unfinished" in payload:
                payload["unfinished"] = payload["unfinished"] == "1"
            result = router.get(route, payload)
            if result is not None:
                return self.respond(result)
            path = router.file(route)
            if path is None:
                return self.respond({"error": "文件不存在"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.trusted():
                return self.respond({"error": "仅允许本机同源访问"}, 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond({"error": "请求格式必须为 JSON"}, 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > service.config["max_upload_mb"] * 3 * 1024 * 1024:
                    return self.respond({"error": "请求内容为空或超过大小限制"}, 413)
                content = self.rfile.read(size)
                if len(content) != size:
                    raise ValueError("请求内容未传输完整，请重新提交")
                payload = json_object(content)
                result = router.post(self.path, payload)
                if result is None:
                    return self.respond({"error": "接口不存在"}, 404)
                self.respond(result)
            except LedgerCorruptionError as exc:
                self.respond({"error": str(exc)}, 503)
            except TimeoutError:
                self.respond({"error": "请求上传超时，账本未提交，请重新导入"}, 408)
            except (ValueError, KeyError, TypeError) as exc:
                self.respond({"error": str(exc)}, 400)
            except Exception as exc:
                self.log_error("%s", str(exc))
                self.respond({"error": "文件读取或保存失败。请检查 Excel 是否有效、输出目录是否可写，再重试"}, 500)

    return ThreadingHTTPServer((host, port), Handler)
