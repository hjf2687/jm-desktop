"""本地收藏夹：JSON 持久化。"""
import json
import os
import time


class FavoritesStore:
    def __init__(self, path: str):
        self.path = path
        self.items = []  # [{id, name, time}]
        self.load()

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                self.items = json.load(f)
        except (OSError, ValueError):
            self.items = []

    def save(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)

    def has(self, aid) -> bool:
        aid = str(aid)
        return any(it['id'] == aid for it in self.items)

    def add(self, aid, name: str):
        aid = str(aid)
        if self.has(aid):
            return
        self.items.insert(0, {'id': aid, 'name': name, 'time': int(time.time())})
        self.save()

    def remove(self, aid):
        aid = str(aid)
        self.items = [it for it in self.items if it['id'] != aid]
        self.save()
