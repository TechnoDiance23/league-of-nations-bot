import json
import os
import threading
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

class StorageManager:
    """Thread-safe JSON persistence storage manager for diplomacy & voting data."""

    def __init__(self, filename: str):
        self.file_path = os.path.join(DATA_DIR, filename)
        self.lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2, ensure_ascii=False)

    def read(self) -> Dict[str, Any]:
        with self.lock:
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}

    def write(self, data: Dict[str, Any]):
        with self.lock:
            temp_path = self.file_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.file_path)

    def get(self, key: str, default: Any = None) -> Any:
        data = self.read()
        return data.get(key, default)

    def set(self, key: str, value: Any):
        data = self.read()
        data[key] = value
        self.write(data)

    def delete(self, key: str):
        data = self.read()
        if key in data:
            del data[key]
            self.write(data)

# Singleton storage instances
votes_storage = StorageManager("active_votes.json")
resolutions_storage = StorageManager("resolutions.json")
delegates_storage = StorageManager("delegates.json")
assembly_storage = StorageManager("assembly.json")
countries_storage = StorageManager("countries.json")
gazette_storage = StorageManager("gazette.json")
embassies_storage = StorageManager("embassies.json")
ambassador_appointments_storage = StorageManager("ambassador_appointments.json")
ambassador_rooms_storage = StorageManager("ambassador_rooms.json")
translation_channels_storage = StorageManager("translation_channels.json")
treaties_storage = StorageManager("treaties.json")
icj_storage = StorageManager("icj_cases.json")
precedents_storage = StorageManager("icj_precedents.json")
prosecution_storage = StorageManager("prosecution_inquiries.json")
signatories_storage = StorageManager("charter_signatories.json")
tickets_storage = StorageManager("tickets.json")
sticky_roles_storage = StorageManager("sticky_roles.json")
moderation_storage = StorageManager("moderation.json")
applications_storage = StorageManager("applications.json")
summits_storage = StorageManager("summits.json")
