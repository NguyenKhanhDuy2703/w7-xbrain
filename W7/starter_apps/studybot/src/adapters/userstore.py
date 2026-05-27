"""User state DB adapters. Pick via USERSTORE_BACKEND env var.

Interface:
    add_doc(user_id, doc_id, metadata: dict) -> None
    list_docs(user_id) -> list[dict]
    log_query(user_id, query, answer) -> None
    recent_queries(user_id, limit=10) -> list[dict]
"""
import json
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DynamoDBUserStore:
    """Single-table design: PK=user_id, SK=DOC#<doc_id> or QUERY#<timestamp>."""

    def __init__(self, table_name: str, region: str):
        import boto3
        if not table_name:
            raise ValueError("USERSTORE_TABLE must be set for DynamoDB backend")
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def add_doc(self, user_id: str, doc_id: str, metadata: dict) -> None:
        self.table.put_item(
            Item={
                "user_id": user_id,
                "sk": f"DOC#{doc_id}",
                "doc_id": doc_id,
                "created_at": _now(),
                **metadata,
            }
        )

    def list_docs(self, user_id: str) -> list:
        resp = self.table.query(
            KeyConditionExpression="user_id = :u AND begins_with(sk, :p)",
            ExpressionAttributeValues={":u": user_id, ":p": "DOC#"},
        )
        return resp.get("Items", [])

    def log_query(self, user_id: str, query: str, answer: str) -> None:
        ts = _now()
        self.table.put_item(
            Item={
                "user_id": user_id,
                "sk": f"QUERY#{ts}",
                "query": query,
                "answer": answer[:1000],
                "created_at": ts,
            }
        )

    def recent_queries(self, user_id: str, limit: int = 10) -> list:
        resp = self.table.query(
            KeyConditionExpression="user_id = :u AND begins_with(sk, :p)",
            ExpressionAttributeValues={":u": user_id, ":p": "QUERY#"},
            ScanIndexForward=False,
            Limit=limit,
        )
        return resp.get("Items", [])

    def add_flashcard(self, user_id: str, doc_id: str, flashcard_id: str, question: str, answer: str) -> None:
        self.table.put_item(
            Item={
                "user_id": user_id,
                "sk": f"FLASHCARD#{flashcard_id}",
                "doc_id": doc_id,
                "flashcard_id": flashcard_id,
                "question": question,
                "answer": answer,
                "created_at": _now(),
            }
        )

    def list_flashcards(self, user_id: str, doc_id: str = None) -> list:
        resp = self.table.query(
            KeyConditionExpression="user_id = :u AND begins_with(sk, :p)",
            ExpressionAttributeValues={":u": user_id, ":p": "FLASHCARD#"},
        )
        items = resp.get("Items", [])
        if doc_id:
            items = [item for item in items if item.get("doc_id") == doc_id]
        return items

    def delete_flashcard(self, user_id: str, flashcard_id: str) -> None:
        self.table.delete_item(
            Key={
                "user_id": user_id,
                "sk": f"FLASHCARD#{flashcard_id}",
            }
        )

    def add_quiz_question(self, user_id: str, doc_id: str, quiz_id: str, question: str, options: list, correct_option: int, explanation: str) -> None:
        self.table.put_item(
            Item={
                "user_id": user_id,
                "sk": f"QUIZ#{quiz_id}",
                "doc_id": doc_id,
                "quiz_id": quiz_id,
                "question": question,
                "options": options,
                "correct_option": correct_option,
                "explanation": explanation,
                "created_at": _now(),
            }
        )

    def list_quizzes(self, user_id: str, doc_id: str = None) -> list:
        resp = self.table.query(
            KeyConditionExpression="user_id = :u AND begins_with(sk, :p)",
            ExpressionAttributeValues={":u": user_id, ":p": "QUIZ#"},
        )
        items = resp.get("Items", [])
        if doc_id:
            items = [item for item in items if item.get("doc_id") == doc_id]
        return items

    def delete_quiz_question(self, user_id: str, quiz_id: str) -> None:
        self.table.delete_item(
            Key={
                "user_id": user_id,
                "sk": f"QUIZ#{quiz_id}",
            }
        )




class PostgresUserStore:
    def __init__(self, url: str):
        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2 not installed. Run: pip install psycopg2-binary"
            )
        if not url:
            raise ValueError("USERSTORE_POSTGRES_URL must be set for Postgres backend")
        self.conn = psycopg2.connect(url)
        self.conn.autocommit = True
        self._init_schema()

    def _init_schema(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_docs (
                    user_id TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    metadata JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (user_id, doc_id)
                );
                CREATE TABLE IF NOT EXISTS user_queries (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    query TEXT,
                    answer TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS user_flashcards (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS user_quizzes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options JSONB NOT NULL,
                    correct_option INTEGER NOT NULL,
                    explanation TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS user_queries_user_idx ON user_queries(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS user_flashcards_user_idx ON user_flashcards(user_id, doc_id);
                CREATE INDEX IF NOT EXISTS user_quizzes_user_idx ON user_quizzes(user_id, doc_id);
            """)



    def add_doc(self, user_id, doc_id, metadata):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_docs (user_id, doc_id, metadata) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, doc_id) DO UPDATE SET metadata = EXCLUDED.metadata",
                (user_id, doc_id, json.dumps(metadata)),
            )

    def list_docs(self, user_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, metadata, created_at FROM user_docs WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            return [
                {"doc_id": r[0], **(r[1] or {}), "created_at": r[2].isoformat()}
                for r in cur.fetchall()
            ]

    def log_query(self, user_id, query, answer):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_queries (user_id, query, answer) VALUES (%s, %s, %s)",
                (user_id, query, answer[:1000]),
            )

    def recent_queries(self, user_id, limit=10):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT query, answer, created_at FROM user_queries WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
            )
            return [
                {"query": r[0], "answer": r[1], "created_at": r[2].isoformat()}
                for r in cur.fetchall()
            ]

    def add_flashcard(self, user_id: str, doc_id: str, flashcard_id: str, question: str, answer: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_flashcards (id, user_id, doc_id, question, answer) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET question = EXCLUDED.question, answer = EXCLUDED.answer",
                (flashcard_id, user_id, doc_id, question, answer),
            )

    def list_flashcards(self, user_id: str, doc_id: str = None) -> list:
        with self.conn.cursor() as cur:
            if doc_id:
                cur.execute(
                    "SELECT id, doc_id, question, answer, created_at FROM user_flashcards WHERE user_id = %s AND doc_id = %s ORDER BY created_at DESC",
                    (user_id, doc_id),
                )
            else:
                cur.execute(
                    "SELECT id, doc_id, question, answer, created_at FROM user_flashcards WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
            return [
                {"id": r[0], "doc_id": r[1], "question": r[2], "answer": r[3], "created_at": r[4].isoformat()}
                for r in cur.fetchall()
            ]

    def delete_flashcard(self, user_id: str, flashcard_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_flashcards WHERE user_id = %s AND id = %s",
                (user_id, flashcard_id),
            )

    def add_quiz_question(self, user_id: str, doc_id: str, quiz_id: str, question: str, options: list, correct_option: int, explanation: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_quizzes (id, user_id, doc_id, question, options, correct_option, explanation) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET question = EXCLUDED.question, options = EXCLUDED.options, correct_option = EXCLUDED.correct_option, explanation = EXCLUDED.explanation",
                (quiz_id, user_id, doc_id, question, json.dumps(options), correct_option, explanation),
            )

    def list_quizzes(self, user_id: str, doc_id: str = None) -> list:
        with self.conn.cursor() as cur:
            if doc_id:
                cur.execute(
                    "SELECT id, doc_id, question, options, correct_option, explanation, created_at FROM user_quizzes WHERE user_id = %s AND doc_id = %s ORDER BY created_at DESC",
                    (user_id, doc_id),
                )
            else:
                cur.execute(
                    "SELECT id, doc_id, question, options, correct_option, explanation, created_at FROM user_quizzes WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
            return [
                {
                    "id": r[0], "doc_id": r[1], "question": r[2], 
                    "options": r[3] if isinstance(r[3], list) else json.loads(r[3]), 
                    "correct_option": r[4], "explanation": r[5], "created_at": r[6].isoformat()
                }
                for r in cur.fetchall()
            ]

    def delete_quiz_question(self, user_id: str, quiz_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_quizzes WHERE user_id = %s AND id = %s",
                (user_id, quiz_id),
            )




class SQLiteUserStore:
    """Local dev store. NOT for production — single-file, no concurrency, no scaling."""

    def __init__(self, db_path: str):
        import sqlite3
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_docs (
                user_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, doc_id)
            );
            CREATE TABLE IF NOT EXISTS user_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                query TEXT,
                answer TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_flashcards (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_quizzes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                question TEXT NOT NULL,
                options TEXT NOT NULL,
                correct_option INTEGER NOT NULL,
                explanation TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS user_queries_user_idx ON user_queries(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS user_flashcards_user_idx ON user_flashcards(user_id, doc_id);
            CREATE INDEX IF NOT EXISTS user_quizzes_user_idx ON user_quizzes(user_id, doc_id);
        """)
        self.conn.commit()



    def add_doc(self, user_id, doc_id, metadata):
        self.conn.execute(
            "INSERT OR REPLACE INTO user_docs (user_id, doc_id, metadata) VALUES (?, ?, ?)",
            (user_id, doc_id, json.dumps(metadata)),
        )
        self.conn.commit()

    def list_docs(self, user_id):
        cur = self.conn.execute(
            "SELECT doc_id, metadata, created_at FROM user_docs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [
            {"doc_id": r[0], **(json.loads(r[1]) if r[1] else {}), "created_at": r[2]}
            for r in cur.fetchall()
        ]

    def log_query(self, user_id, query, answer):
        self.conn.execute(
            "INSERT INTO user_queries (user_id, query, answer) VALUES (?, ?, ?)",
            (user_id, query, answer[:1000]),
        )
        self.conn.commit()

    def recent_queries(self, user_id, limit=10):
        cur = self.conn.execute(
            "SELECT query, answer, created_at FROM user_queries WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [
            {"query": r[0], "answer": r[1], "created_at": r[2]}
            for r in cur.fetchall()
        ]

    def add_flashcard(self, user_id: str, doc_id: str, flashcard_id: str, question: str, answer: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO user_flashcards (id, user_id, doc_id, question, answer) VALUES (?, ?, ?, ?, ?)",
            (flashcard_id, user_id, doc_id, question, answer),
        )
        self.conn.commit()

    def list_flashcards(self, user_id: str, doc_id: str = None) -> list:
        if doc_id:
            cur = self.conn.execute(
                "SELECT id, doc_id, question, answer, created_at FROM user_flashcards WHERE user_id = ? AND doc_id = ? ORDER BY created_at DESC",
                (user_id, doc_id),
            )
        else:
            cur = self.conn.execute(
                "SELECT id, doc_id, question, answer, created_at FROM user_flashcards WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
        return [
            {"id": r[0], "doc_id": r[1], "question": r[2], "answer": r[3], "created_at": r[4]}
            for r in cur.fetchall()
        ]

    def delete_flashcard(self, user_id: str, flashcard_id: str) -> None:
        self.conn.execute(
            "DELETE FROM user_flashcards WHERE user_id = ? AND id = ?",
            (user_id, flashcard_id),
        )
        self.conn.commit()

    def add_quiz_question(self, user_id: str, doc_id: str, quiz_id: str, question: str, options: list, correct_option: int, explanation: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO user_quizzes (id, user_id, doc_id, question, options, correct_option, explanation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (quiz_id, user_id, doc_id, question, json.dumps(options), correct_option, explanation),
        )
        self.conn.commit()

    def list_quizzes(self, user_id: str, doc_id: str = None) -> list:
        if doc_id:
            cur = self.conn.execute(
                "SELECT id, doc_id, question, options, correct_option, explanation, created_at FROM user_quizzes WHERE user_id = ? AND doc_id = ? ORDER BY created_at DESC",
                (user_id, doc_id),
            )
        else:
            cur = self.conn.execute(
                "SELECT id, doc_id, question, options, correct_option, explanation, created_at FROM user_quizzes WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
        return [
            {
                "id": r[0], "doc_id": r[1], "question": r[2], 
                "options": json.loads(r[3]) if r[3] else [], 
                "correct_option": r[4], "explanation": r[5], "created_at": r[6]
            }
            for r in cur.fetchall()
        ]

    def delete_quiz_question(self, user_id: str, quiz_id: str) -> None:
        self.conn.execute(
            "DELETE FROM user_quizzes WHERE user_id = ? AND id = ?",
            (user_id, quiz_id),
        )
        self.conn.commit()




class DocumentDBUserStore:
    """MongoDB-compatible store. Works with AWS DocumentDB and MongoDB Atlas.

    DocumentDB requires TLS. Pass USERSTORE_MONGO_TLS_CA env var pointing at the
    AWS RDS CA bundle file (download from AWS docs once).
    """

    def __init__(self, url: str, db_name: str = "studybot", tls_ca_file: str = ""):
        try:
            from pymongo import MongoClient
        except ImportError:
            raise ImportError(
                "pymongo not installed. Run: pip install -r requirements-optional.txt"
            )
        if not url:
            raise ValueError("USERSTORE_MONGO_URL must be set for DocumentDB backend")
        kwargs: dict = {}
        if "documentdb" in url.lower() or tls_ca_file:
            kwargs["tls"] = True
        if tls_ca_file:
            kwargs["tlsCAFile"] = tls_ca_file
        self.client = MongoClient(url, **kwargs)
        self.db = self.client[db_name]
        self.docs = self.db["user_docs"]
        self.queries = self.db["user_queries"]
        self.docs.create_index([("user_id", 1), ("doc_id", 1)], unique=True)
        self.queries.create_index([("user_id", 1), ("created_at", -1)])
        self.flashcards = self.db["user_flashcards"]
        self.flashcards.create_index([("user_id", 1), ("doc_id", 1)])
        self.quizzes = self.db["user_quizzes"]
        self.quizzes.create_index([("user_id", 1), ("doc_id", 1)])



    def add_doc(self, user_id: str, doc_id: str, metadata: dict) -> None:
        self.docs.update_one(
            {"user_id": user_id, "doc_id": doc_id},
            {"$set": {**metadata, "user_id": user_id, "doc_id": doc_id, "created_at": _now()}},
            upsert=True,
        )

    def list_docs(self, user_id: str) -> list:
        return [
            {**{k: v for k, v in d.items() if k != "_id"}}
            for d in self.docs.find({"user_id": user_id}).sort("created_at", -1)
        ]

    def log_query(self, user_id: str, query: str, answer: str) -> None:
        self.queries.insert_one({
            "user_id": user_id, "query": query, "answer": answer[:1000], "created_at": _now(),
        })

    def recent_queries(self, user_id: str, limit: int = 10) -> list:
        return [
            {**{k: v for k, v in q.items() if k != "_id"}}
            for q in self.queries.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
        ]

    def add_flashcard(self, user_id: str, doc_id: str, flashcard_id: str, question: str, answer: str) -> None:
        self.flashcards.update_one(
            {"_id": flashcard_id},
            {"$set": {
                "_id": flashcard_id,
                "user_id": user_id,
                "doc_id": doc_id,
                "question": question,
                "answer": answer,
                "created_at": _now()
            }},
            upsert=True,
        )

    def list_flashcards(self, user_id: str, doc_id: str = None) -> list:
        query = {"user_id": user_id}
        if doc_id:
            query["doc_id"] = doc_id
        return [
            {**{k: v for k, v in f.items() if k != "_id"}, "id": f["_id"]}
            for f in self.flashcards.find(query).sort("created_at", -1)
        ]

    def delete_flashcard(self, user_id: str, flashcard_id: str) -> None:
        self.flashcards.delete_one({"user_id": user_id, "_id": flashcard_id})

    def add_quiz_question(self, user_id: str, doc_id: str, quiz_id: str, question: str, options: list, correct_option: int, explanation: str) -> None:
        self.quizzes.update_one(
            {"_id": quiz_id},
            {"$set": {
                "_id": quiz_id,
                "user_id": user_id,
                "doc_id": doc_id,
                "question": question,
                "options": options,
                "correct_option": correct_option,
                "explanation": explanation,
                "created_at": _now()
            }},
            upsert=True,
        )

    def list_quizzes(self, user_id: str, doc_id: str = None) -> list:
        query = {"user_id": user_id}
        if doc_id:
            query["doc_id"] = doc_id
        return [
            {**{k: v for k, v in q.items() if k != "_id"}, "id": q["_id"]}
            for q in self.quizzes.find(query).sort("created_at", -1)
        ]

    def delete_quiz_question(self, user_id: str, quiz_id: str) -> None:
        self.quizzes.delete_one({"user_id": user_id, "_id": quiz_id})




class MySQLUserStore:
    """RDS MySQL / Aurora MySQL adapter via pymysql. Schema mirrors PostgresUserStore."""

    def __init__(self, url: str):
        try:
            import pymysql
            from urllib.parse import urlparse
        except ImportError:
            raise ImportError("pymysql not installed. Run: pip install -r requirements-optional.txt")
        if not url:
            raise ValueError("USERSTORE_MYSQL_URL must be set for MySQL backend")
        p = urlparse(url)
        self.conn = pymysql.connect(
            host=p.hostname,
            port=p.port or 3306,
            user=p.username,
            password=p.password,
            database=p.path.lstrip("/"),
            charset="utf8mb4",
            autocommit=True,
        )
        self._init_schema()

    def _init_schema(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_docs (
                    user_id VARCHAR(255) NOT NULL,
                    doc_id VARCHAR(255) NOT NULL,
                    metadata JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, doc_id)
                ) CHARACTER SET utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_queries (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    query TEXT,
                    answer TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_created (user_id, created_at)
                ) CHARACTER SET utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_flashcards (
                    id VARCHAR(255) NOT NULL,
                    user_id VARCHAR(255) NOT NULL,
                    doc_id VARCHAR(255) NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    INDEX idx_user_doc (user_id, doc_id)
                ) CHARACTER SET utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_quizzes (
                    id VARCHAR(255) NOT NULL,
                    user_id VARCHAR(255) NOT NULL,
                    doc_id VARCHAR(255) NOT NULL,
                    question TEXT NOT NULL,
                    options JSON NOT NULL,
                    correct_option INTEGER NOT NULL,
                    explanation TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    INDEX idx_user_doc (user_id, doc_id)
                ) CHARACTER SET utf8mb4
            """)



    def add_doc(self, user_id, doc_id, metadata):
        with self.conn.cursor() as cur:
            cur.execute(
                "REPLACE INTO user_docs (user_id, doc_id, metadata) VALUES (%s, %s, %s)",
                (user_id, doc_id, json.dumps(metadata)),
            )

    def list_docs(self, user_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, metadata, created_at FROM user_docs WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            return [
                {"doc_id": r[0], **(json.loads(r[1]) if r[1] else {}), "created_at": str(r[2])}
                for r in cur.fetchall()
            ]

    def log_query(self, user_id, query, answer):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_queries (user_id, query, answer) VALUES (%s, %s, %s)",
                (user_id, query, answer[:1000]),
            )

    def recent_queries(self, user_id, limit=10):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT query, answer, created_at FROM user_queries WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
            )
            return [
                {"query": r[0], "answer": r[1], "created_at": str(r[2])}
                for r in cur.fetchall()
            ]

    def add_flashcard(self, user_id: str, doc_id: str, flashcard_id: str, question: str, answer: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "REPLACE INTO user_flashcards (id, user_id, doc_id, question, answer) VALUES (%s, %s, %s, %s, %s)",
                (flashcard_id, user_id, doc_id, question, answer),
            )

    def list_flashcards(self, user_id: str, doc_id: str = None) -> list:
        with self.conn.cursor() as cur:
            if doc_id:
                cur.execute(
                    "SELECT id, doc_id, question, answer, created_at FROM user_flashcards WHERE user_id = %s AND doc_id = %s ORDER BY created_at DESC",
                    (user_id, doc_id),
                )
            else:
                cur.execute(
                    "SELECT id, doc_id, question, answer, created_at FROM user_flashcards WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
            return [
                {"id": r[0], "doc_id": r[1], "question": r[2], "answer": r[3], "created_at": str(r[4])}
                for r in cur.fetchall()
            ]

    def delete_flashcard(self, user_id: str, flashcard_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_flashcards WHERE user_id = %s AND id = %s",
                (user_id, flashcard_id),
            )

    def add_quiz_question(self, user_id: str, doc_id: str, quiz_id: str, question: str, options: list, correct_option: int, explanation: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "REPLACE INTO user_quizzes (id, user_id, doc_id, question, options, correct_option, explanation) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (quiz_id, user_id, doc_id, question, json.dumps(options), correct_option, explanation),
            )

    def list_quizzes(self, user_id: str, doc_id: str = None) -> list:
        with self.conn.cursor() as cur:
            if doc_id:
                cur.execute(
                    "SELECT id, doc_id, question, options, correct_option, explanation, created_at FROM user_quizzes WHERE user_id = %s AND doc_id = %s ORDER BY created_at DESC",
                    (user_id, doc_id),
                )
            else:
                cur.execute(
                    "SELECT id, doc_id, question, options, correct_option, explanation, created_at FROM user_quizzes WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
            return [
                {
                    "id": r[0], "doc_id": r[1], "question": r[2], 
                    "options": r[3] if isinstance(r[3], list) else json.loads(r[3]), 
                    "correct_option": r[4], "explanation": r[5], "created_at": str(r[6])
                }
                for r in cur.fetchall()
            ]

    def delete_quiz_question(self, user_id: str, quiz_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_quizzes WHERE user_id = %s AND id = %s",
                (user_id, quiz_id),
            )


