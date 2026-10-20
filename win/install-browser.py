"""Playwright browser installer for kroger-aio on Windows.

Run this once after installing kroger-aio to download the Chromium
browser required for coupon automation.

Usage: python install-browser.py
Or:    python -m playwright install chromium
"""
import sys
import subprocess


def main():
    print("Downloading Playwright Chromium browser...")
    print("This will take about 2 minutes.")
    print()

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True
        )
        print()
        print("Browser installed successfully!")
        print()
        print("You can now run: kroger-aio.exe menu")
    except subprocess.CalledProcessError as e:
        print(f"Browser install failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
