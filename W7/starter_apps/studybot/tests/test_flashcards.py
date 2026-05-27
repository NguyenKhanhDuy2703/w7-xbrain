import os
import sys
import tempfile
from pathlib import Path

# Ensure all local backends BEFORE importing app
os.environ.setdefault("AI_BACKEND", "local")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("USERSTORE_BACKEND", "sqlite")
os.environ.setdefault("VECTOR_BACKEND", "local")

# Per-test temp dirs to avoid cross-pollution
_tmp = tempfile.mkdtemp(prefix="studybot-fc-test-")
os.environ["STORAGE_LOCAL_DIR"] = str(Path(_tmp) / "uploads")
os.environ["USERSTORE_SQLITE_PATH"] = str(Path(_tmp) / "users.db")

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from src.app import app

client = TestClient(app)

def test_flashcards_lifecycle():
    # 1. Upload doc
    content = b"Photosynthesis is a process used by plants to convert light energy into chemical energy."
    r = client.post(
        "/upload",
        files={"file": ("biology.txt", content, "text/plain")},
        headers={"X-User-Id": "studious-student"},
    )
    assert r.status_code == 200
    doc_id = r.json()["doc_id"]
    
    # 2. Generate flashcards
    r_gen = client.post(
        "/flashcards/generate",
        json={"doc_id": doc_id, "count": 3},
        headers={"X-User-Id": "studious-student"},
    )
    assert r_gen.status_code == 200, r_gen.text
    gen_body = r_gen.json()
    assert gen_body["doc_id"] == doc_id
    assert len(gen_body["flashcards"]) == 3
    
    # Check shape
    fc = gen_body["flashcards"][0]
    assert "id" in fc
    assert "question" in fc
    assert "answer" in fc
    
    # 3. List flashcards
    r_list = client.get(
        "/flashcards",
        params={"doc_id": doc_id},
        headers={"X-User-Id": "studious-student"},
    )
    assert r_list.status_code == 200
    list_body = r_list.json()
    assert len(list_body["flashcards"]) == 3
    
    # 4. Delete one
    fc_to_delete = list_body["flashcards"][0]["id"]
    r_del = client.delete(
        f"/flashcards/{fc_to_delete}",
        headers={"X-User-Id": "studious-student"},
    )
    assert r_del.status_code == 200
    assert r_del.json()["status"] == "deleted"
    
    # 5. List again, should have 2 left
    r_list2 = client.get(
        "/flashcards",
        params={"doc_id": doc_id},
        headers={"X-User-Id": "studious-student"},
    )
    assert len(r_list2.json()["flashcards"]) == 2
