import ctypes
import traceback
from pathlib import Path


def main() -> None:
    try:
        from Bot import bot

        bot.start()
    except Exception:
        error = traceback.format_exc()
        Path(__file__).with_name("startup-error.log").write_text(
            error, encoding="utf-8"
        )
        ctypes.windll.user32.MessageBoxW(
            0,
            "EVE Mining could not start. Details were saved to startup-error.log.",
            "EVE Mining startup error",
            0x10,
        )


if __name__ == "__main__":
    main()
