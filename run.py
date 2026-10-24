import os

from waitress import serve
from app import create_app


app = create_app()


def cleanup_import_storage(application):
    """Drop import previews that expired while the service was down."""
    from app.services.import_storage_service import cleanup_expired, storage_dir

    if not os.path.isdir(storage_dir(application)):
        return
    try:
        stats = cleanup_expired(application)
    except OSError as exc:
        application.logger.warning('Import cleanup at startup failed: errno=%s', exc.errno)
        return
    if stats.removed:
        application.logger.info('Import cleanup at startup removed %s expired files', stats.removed)


with app.app_context():
    cleanup_import_storage(app)


def browser_url(host, port):
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{browser_host}:{port}"


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    print(f"Server is starting on {host}:{port}", flush=True)
    print(f"Open in browser: {browser_url(host, port)}", flush=True)
    serve(app, host=host, port=port)
