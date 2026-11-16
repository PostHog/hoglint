import subprocess
import sys


def build_skills() -> None:
    # Runs inventory's logic module as a script.
    subprocess.Popen([sys.executable, "-m", "products.inventory.backend.logic.stock"])
