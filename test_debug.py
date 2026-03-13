import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path("src/fofproject/.env"))

from fofproject.load import continue_running, init_funds

gpt_fund_2 = continue_running(folder_path="input/greenwoods", save=True)
gpt_fund_2 = init_funds(gpt_fund_2)
