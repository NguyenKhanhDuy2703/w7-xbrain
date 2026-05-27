"""Endpoint handlers. Pure business logic — knows nothing about FastAPI or AWS specifics."""
import io
import uuid
from typing import Optional


PROMPT_TEMPLATE = """You are a study assistant. Answer the student's question using ONLY the
context retrieved from their uploaded lecture notes. Cite the source by chunk
number where possible. If the context does not contain the answer, say so
plainly. Do not invent information.

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""


def _extract_text(filename: str, data: bytes) -> str:
    """Extract plain text from PDF or .txt upload."""
    name = filename.lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            return "(pypdf not installed — install requirements.txt)"
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    # Default: assume UTF-8 text
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def handle_upload(
    user_id: str,
    filename: str,
    data: bytes,
    storage,
    userstore,
    vector_store,
) -> dict:
    """Store the file, extract text, ingest into vector store, record in userstore."""
    doc_id = str(uuid.uuid4())
    key = f"{user_id}/{doc_id}/{filename}"
    location = storage.put(key, data)
    text = _extract_text(filename, data)
    if text.strip():
        vector_store.ingest(doc_id=doc_id, text=text, metadata={"user_id": user_id, "filename": filename})
    userstore.add_doc(
        user_id=user_id,
        doc_id=doc_id,
        metadata={"filename": filename, "size": len(data), "location": location, "chars": len(text)},
    )
    return {
        "doc_id": doc_id,
        "filename": filename,
        "size": len(data),
        "chars_extracted": len(text),
        "location": location,
    }


def handle_query(
    user_id: str,
    question: str,
    ai_client,
    userstore,
    vector_store,
    vector_backend: str,
    bedrock_kb_id: str,
) -> dict:
    """RAG flow: retrieve user's relevant chunks → call AI with context → log + return."""
    if vector_backend == "bedrock_kb":
        # Production path: let Bedrock do retrieve + generate in one call
        result = ai_client.retrieve_and_generate(query=question, kb_id=bedrock_kb_id)
        answer = result["answer"]
        citations = result["citations"]
    else:
        # Local path: do our own retrieve then prompt
        chunks = vector_store.search(question, top_k=5, filter={"user_id": user_id})
        if not chunks:
            answer = "No relevant content found in your uploaded documents. Upload some first."
            citations = []
        else:
            context = "\n\n".join(f"[chunk {i+1}] {c['text']}" for i, c in enumerate(chunks))
            prompt = PROMPT_TEMPLATE.format(context=context, question=question)
            answer = ai_client.invoke(prompt, max_tokens=512)
            citations = [
                {"chunk": i + 1, "doc_id": c["doc_id"], "score": c["score"], "text": c["text"][:200]}
                for i, c in enumerate(chunks)
            ]

    userstore.log_query(user_id=user_id, query=question, answer=answer)
    return {"question": question, "answer": answer, "citations": citations}


def handle_list_docs(user_id: str, userstore) -> dict:
    return {"user_id": user_id, "docs": userstore.list_docs(user_id)}


def handle_recent_queries(user_id: str, userstore, limit: int = 10) -> dict:
    return {"user_id": user_id, "queries": userstore.recent_queries(user_id, limit=limit)}


FLASHCARD_PROMPT_TEMPLATE = """You are a study assistant. Generate a JSON list of flashcards (question and answer pairs) based on the following text.
Generate exactly {count} flashcards.
Each flashcard must have a "question" (concise, clear query about a key concept) and an "answer" (accurate, concise explanation).
Return ONLY a valid JSON array of objects, where each object has "question" and "answer" keys. Do not include markdown formatting or extra text.

TEXT:
{text}
"""

def clean_json_response(text: str) -> str:
    import re
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()

def handle_generate_flashcards(
    user_id: str,
    doc_id: str,
    count: int,
    storage,
    userstore,
    ai_client,
) -> dict:
    import json
    import re
    docs = userstore.list_docs(user_id)
    doc_meta = next((d for d in docs if d["doc_id"] == doc_id), None)
    if not doc_meta:
        raise ValueError(f"Document {doc_id} not found for user")
        
    filename = doc_meta.get("filename", "untitled")
    key = f"{user_id}/{doc_id}/{filename}"
    data = storage.get(key)
    text = _extract_text(filename, data)
    if not text.strip():
        raise ValueError("Document has no extractable text")
        
    sample_text = text[:8000]
    prompt = FLASHCARD_PROMPT_TEMPLATE.format(count=count, text=sample_text)
    ai_response = ai_client.invoke(prompt, max_tokens=1024)
    
    if ai_response.startswith("[LOCAL_AI_STUB]"):
        cards = [
            {"question": f"What is the main topic of {filename}?", "answer": f"The main topic centers around the contents of {filename}."},
            {"question": "What is the key takeaway?", "answer": "The document provides a detailed overview of the subject matter."},
            {"question": "Why is this subject important?", "answer": "It forms the foundation of the concepts discussed in this chapter."}
        ][:count]
    else:
        cleaned = clean_json_response(ai_response)
        try:
            cards = json.loads(cleaned)
            if not isinstance(cards, list):
                if isinstance(cards, dict) and "flashcards" in cards:
                    cards = cards["flashcards"]
                else:
                    raise ValueError("AI response did not parse as a list of flashcards")
        except Exception as e:
            try:
                items = re.findall(r'\{\s*"question"\s*:\s*"(.*?)"\s*,\s*"answer"\s*:\s*"(.*?)"\s*\}', cleaned, re.DOTALL)
                cards = [{"question": q.strip(), "answer": a.strip()} for q, a in items]
            except Exception:
                cards = []
            if not cards:
                raise ValueError(f"Failed to parse AI response: {ai_response}") from e
                
    saved_cards = []
    for card in cards:
        flashcard_id = str(uuid.uuid4())
        q = card.get("question", "Question")
        a = card.get("answer", "Answer")
        userstore.add_flashcard(
            user_id=user_id,
            doc_id=doc_id,
            flashcard_id=flashcard_id,
            question=q,
            answer=a,
        )
        saved_cards.append({
            "id": flashcard_id,
            "doc_id": doc_id,
            "question": q,
            "answer": a,
        })
        
    return {"doc_id": doc_id, "flashcards": saved_cards}

def handle_list_flashcards(user_id: str, doc_id: Optional[str], userstore) -> dict:
    return {"user_id": user_id, "flashcards": userstore.list_flashcards(user_id, doc_id)}

def handle_delete_flashcard(user_id: str, flashcard_id: str, userstore) -> dict:
    userstore.delete_flashcard(user_id, flashcard_id)
    return {"status": "deleted", "flashcard_id": flashcard_id}


QUIZ_PROMPT_TEMPLATE = """You are a study assistant. Generate a multiple-choice quiz based on the following text.
Generate exactly {count} questions.
Each question must have:
1. "question": string (the query)
2. "options": a list of exactly 4 strings (options)
3. "correct_option": integer (0 to 3 index of correct option in options)
4. "explanation": string explaining why the correct option is correct.

Return ONLY a valid JSON array of objects. Do not include markdown formatting or extra text.

TEXT:
{text}
"""

def handle_generate_quiz(
    user_id: str,
    doc_id: str,
    count: int,
    storage,
    userstore,
    ai_client,
) -> dict:
    import json
    import re
    docs = userstore.list_docs(user_id)
    doc_meta = next((d for d in docs if d["doc_id"] == doc_id), None)
    if not doc_meta:
        raise ValueError(f"Document {doc_id} not found for user")
        
    filename = doc_meta.get("filename", "untitled")
    key = f"{user_id}/{doc_id}/{filename}"
    data = storage.get(key)
    text = _extract_text(filename, data)
    if not text.strip():
        raise ValueError("Document has no extractable text")
        
    sample_text = text[:8000]
    prompt = QUIZ_PROMPT_TEMPLATE.format(count=count, text=sample_text)
    ai_response = ai_client.invoke(prompt, max_tokens=1536)
    
    if ai_response.startswith("[LOCAL_AI_STUB]"):
        questions = [
            {
                "question": f"What is the primary subject of {filename}?",
                "options": ["Subject A", "Subject B", "Subject C", "Subject D"],
                "correct_option": 0,
                "explanation": "Subject A is the correct answer based on the title and starting paragraph."
            },
            {
                "question": "Which of the following is true?",
                "options": ["Option 1 is correct", "Option 2 is false", "Option 3 is incorrect", "All of the above"],
                "correct_option": 3,
                "explanation": "Option 1 and 2 are true, making All of the above the correct answer."
            },
            {
                "question": "Why is this system being tested?",
                "options": ["To break it", "To ensure it is correct", "To make it slower", "None of the above"],
                "correct_option": 1,
                "explanation": "Ensuring correctness is the standard objective of software testing."
            }
        ][:count]
    else:
        cleaned = clean_json_response(ai_response)
        try:
            questions = json.loads(cleaned)
            if not isinstance(questions, list):
                if isinstance(questions, dict) and "questions" in questions:
                    questions = questions["questions"]
                else:
                    raise ValueError("AI response did not parse as a list of questions")
        except Exception as e:
            # simple regex parser fallback
            try:
                # Find JSON objects inside array
                objs = re.findall(r'\{[^{}]*\}', cleaned, re.DOTALL)
                questions = []
                for obj in objs:
                    try:
                        q_data = json.loads(obj)
                        if "question" in q_data and "options" in q_data:
                            questions.append(q_data)
                    except Exception:
                        pass
            except Exception:
                questions = []
            if not questions:
                raise ValueError(f"Failed to parse AI response: {ai_response}") from e
                
    saved_quizzes = []
    for q_item in questions:
        quiz_id = str(uuid.uuid4())
        q_text = q_item.get("question", "Question?")
        opts = q_item.get("options", ["A", "B", "C", "D"])
        correct = int(q_item.get("correct_option", 0))
        expl = q_item.get("explanation", "No explanation provided.")
        
        userstore.add_quiz_question(
            user_id=user_id,
            doc_id=doc_id,
            quiz_id=quiz_id,
            question=q_text,
            options=opts,
            correct_option=correct,
            explanation=expl,
        )
        saved_quizzes.append({
            "id": quiz_id,
            "doc_id": doc_id,
            "question": q_text,
            "options": opts,
            "correct_option": correct,
            "explanation": expl,
        })
        
    return {"doc_id": doc_id, "quizzes": saved_quizzes}

def handle_list_quizzes(user_id: str, doc_id: Optional[str], userstore) -> dict:
    return {"user_id": user_id, "quizzes": userstore.list_quizzes(user_id, doc_id)}

def handle_delete_quiz(user_id: str, quiz_id: str, userstore) -> dict:
    userstore.delete_quiz_question(user_id, quiz_id)
    return {"status": "deleted", "quiz_id": quiz_id}


