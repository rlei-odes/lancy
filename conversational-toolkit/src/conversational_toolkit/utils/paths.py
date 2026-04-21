import os


class Paths:
    ROOT_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    DATA_FOLDER = os.path.join(ROOT_FOLDER, "data")
    NOTEBOOKS_FOLDER = os.path.join(ROOT_FOLDER, "notebooks")
    DIST_FOLDER = os.path.join(ROOT_FOLDER, "dist")
    NEXT_FOLDER = os.path.join(DIST_FOLDER, "_next")
    ASSETS_FOLDER = os.path.join(DIST_FOLDER, "assets")
    LOGS_FOLDER = os.path.join(ROOT_FOLDER, "logs")
    CONFIG_FOLDER = os.path.join(ROOT_FOLDER, "src", "config")
