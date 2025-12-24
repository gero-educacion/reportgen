import requests
import json
from datetime import datetime
from pathlib import Path

URL = "https://script.google.com/macros/s/AKfycbxNwaIN2nG9KfnOEkV3_dgDqjJ5DODP9zpewu19UdENht5VKWr9OgcFFRnIvXQsGSSf/exec"

def fetch_data():
    response = requests.get(URL, timeout=30)
    response.raise_for_status() 
    return response.json()

def save_json(data, filename="data.json"):
    data_storage = Path(__file__).resolve().parent.parent / "data"
    path = data_storage / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved JSON → {path}")

def main():
    print("Fetching data from Apps Script...")
    data = fetch_data()
    save_json(data)

if __name__ == "__main__":
    main()
