"""
OpenVelo Studio: main application entry point.

Run directly with:
    python app.py
"""
import argparse
import webbrowser
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="OpenVelo: open-source video bike fitting")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8501, help="Port to bind (default: 8501)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    parser.add_argument("--reload", action="store_true", help="Enable live auto-reload for development")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"\n  OpenVelo 2.0 is running at {url}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        import threading
        import time

        def open_url():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=open_url, daemon=True).start()

    uvicorn.run("src.web.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
