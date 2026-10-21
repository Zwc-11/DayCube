import argparse

import uvicorn

from .api import create_capture_app


def main():
    parser = argparse.ArgumentParser(description="Start the opt-in browser metadata receiver")
    parser.add_argument("--receive", action="store_true", help="Allow incoming browser metadata; paused otherwise")
    args = parser.parse_args()
    uvicorn.run(create_capture_app(receive=args.receive), host="127.0.0.1", port=7700)


if __name__ == "__main__":
    main()
