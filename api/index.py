import os
import sys
from pathlib import Path

# Ensure app directory is on Python path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import app

# Vercel Serverless Function entry point
handler = app
