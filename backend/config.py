from pathlib import Path
import os

BASE_DIR = Path(__file__).parent.parent

class Config:
    DRY_RUN: bool = False  # Software modalità reale

    DB_PATH:  Path = BASE_DIR / "data" / "system_transparency.db"
    LOG_DIR:  Path = BASE_DIR / "log"

    SCAN_TARGETS: list[str] = [
        os.path.normpath(os.path.expanduser("~/Downloads")),
        os.path.normpath(os.path.expanduser("~/Documents")),
        os.path.normpath(os.path.expanduser("~/Desktop")),
    ]
    FILE_THRESHOLD_DAYS: int   = 120
    APP_THRESHOLD_DAYS:  int   = 180
    MIN_SIZE_GB:         float = 0.01
    RECURSIVE:           bool  = False
    CLEANUP_BATCH_SIZE:  int   = 500

cfg = Config()