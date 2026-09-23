"""Local jury UI, using unchanged official evaluator in bounded child processes."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import webbrowser

from jury_eval import run_isolated, version

ROOT = Path(__file__).resolve().parent


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port):
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)
        try:
            self.latest = run_isolated(42)
        except Exception:
            self.server_close()
            raise


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, kind="application/json; charset=utf-8", **headers):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        for key, value in {"Content-Type": kind, "Content-Length": str(len(body)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **headers}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/":
            self.respond(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif route == "/api/health":
            self.respond(200, {"ok": True, "source": "official_participant_mock", "version": version()})
        elif route == "/api/latest":
            self.respond(200, {"report": self.server.latest})
        elif route == "/api/validation":
            path = ROOT / "artifacts" / "validation.json"
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                self.respond(200, {"current": report.get("version") == version(), "report": report})
            except (OSError, ValueError):
                self.respond(200, {"current": False, "report": None})
        elif route == "/api/submission":
            report = self.server.latest
            if not report.get("ok"):
                self.respond(409, {"error": "Сначала выполните успешный запуск."})
            else:
                name = ("submission.csv" if report["seed"] == 42 and report["llm_status"] != "applied"
                        else f"campaigns-seed-{report['seed']}.csv")
                self.respond(200, report["csv"], "text/csv; charset=utf-8", **{"Content-Disposition": f'attachment; filename="{name}"'})
        else:
            self.respond(404, {"error": "Страница не найдена."})

    def do_POST(self):
        if urlsplit(self.path).path != "/api/run":
            self.respond(404, {"error": "Страница не найдена."})
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
            self.respond(403, {"error": "Запуск доступен из локальной панели."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                raise ValueError("Ожидается небольшой JSON-запрос.")
            settings = json.loads(self.rfile.read(length))
            if not isinstance(settings, dict) or set(settings) != {"seed"}:
                raise ValueError("Укажите только seed.")
            seed = settings["seed"]
            if type(seed) is not int or not 0 <= seed <= 1000000:
                raise ValueError("Seed должен быть целым числом от 0 до 1 000 000.")
        except (ValueError, UnicodeError):
            self.respond(400, {"error": "Некорректный seed или формат запроса."})
            return
        if not self.server.lock.acquire(blocking=False):
            self.respond(409, {"error": "Дождитесь завершения текущего запуска."})
            return
        try:
            self.server.latest = run_isolated(seed)
            self.respond(200, {"report": self.server.latest})
        except (RuntimeError, OSError, subprocess.SubprocessError, ValueError):
            self.server.latest = {"ok": False, "error": "Запуск не завершился корректно. Повторите проверку."}
            self.respond(500, {"error": "Ошибка или таймаут запуска официального evaluator."})
        finally:
            self.server.lock.release()


def serve(port=8765, *, open_browser=True):
    with Server(port) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"Cerebrum: {url}\nStop: Ctrl+C", flush=True)
        if open_browser:
            threading.Timer(0.3, webbrowser.open, args=(url,)).start()
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    serve(parser.parse_args().port)
