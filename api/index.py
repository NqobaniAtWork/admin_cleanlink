import os
import sys
from pathlib import Path

# Ensure root directory is on Python path so 'app' is importable
root_path = Path(__file__).resolve().parent.parent
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))

from app.main import app

# Vercel ASGI entry point
app = app
