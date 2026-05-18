import os

from waitress import serve
from app import app


def browser_url(host, port):
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{browser_host}:{port}"


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    print(f"Server is starting on {host}:{port}", flush=True)
    print(f"Open in browser: {browser_url(host, port)}", flush=True)
    serve(app, host=host, port=port)
