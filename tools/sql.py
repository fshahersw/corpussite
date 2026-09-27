"""Read-only SQL against one of the project's SQLite files:  python tools/sql.py <database> "<select ...>" """
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
for row in connection.execute(sys.argv[2]):
    print(*row, sep=' | ')
