from __future__ import annotations

from datetime import datetime
import sys

from colorama import Fore, Style, just_fix_windows_console


just_fix_windows_console()


ASCII_LOGO = r"""
   _____                _ ___         __        ______                 __  _
  / ___/___  ____ ___  (_)   | __  __/ /_____  / ____/_  ___________ _/ /_(_)___  ____
  \__ \/ _ \/ __ `__ \/ / /| |/ / / / __/ __ \/ /   / / / / ___/ __ `/ __/ / __ \/ __ \
 ___/ /  __/ / / / / / / ___ / /_/ / /_/ /_/ / /___/ /_/ / /  / /_/ / /_/ / /_/ / / / /
/____/\___/_/ /_/ /_/_/_/  |_\__,_/\__/\____/\____/\__,_/_/   \__,_/\__/_/\____/_/ /_/
"""


def print_banner() -> None:
    print(f"{Fore.CYAN}{ASCII_LOGO}{Style.RESET_ALL}")


def log_event(level: str, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    color = _level_color(level)
    stream = sys.stderr if level.upper() == "ERROR" else sys.stdout
    print(
        f"{Fore.WHITE}{timestamp}{Style.RESET_ALL} "
        f"{color}[{level.upper()}]{Style.RESET_ALL} "
        f"{message}",
        file=stream,
    )


def log_info(message: str) -> None:
    log_event("INFO", message)


def log_warn(message: str) -> None:
    log_event("WARN", message)


def log_error(message: str) -> None:
    log_event("ERROR", message)


def _level_color(level: str) -> str:
    upper = level.upper()
    if upper == "INFO":
        return Fore.GREEN
    if upper == "WARN":
        return Fore.YELLOW
    if upper == "ERROR":
        return Fore.RED
    return Fore.CYAN
