"""Entry point for the ROM Box Art Finder.

Launch with:  .\\.venv\\Scripts\\python.exe main.py
"""

import tkinter as tk

from gui import ReviewApp


def main() -> None:
    root = tk.Tk()
    ReviewApp(root).run()


if __name__ == "__main__":
    main()
