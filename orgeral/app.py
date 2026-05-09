import os
import json
import sqlite3
import tempfile
from datetime import datetime
from flask import Flask, request, jsonify, render_template, g
from groq import Groq

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB

DB_PATH = os.path.join(os.path.dirname(__file__), "orgeral.db")

SUBJECT_COLORS = {
    "Português": "#27ae60",
    "Matemática": "#2980b9",
    "Ciências": "#8e44ad",
    "História": "#e74c3c",
    "Geografia": "#e67e22",
    "Inglês": "#16a085",
    "Artes": "#bb8fce",
    "Ed. Física": "#c0392b",
    "Religião": "#f1c40f",
    "Outros": "#555555",
}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            date TEXT NOT NULL,
            time TEXT,
            color TEXT DEFAULT '#555555',
            subject TEXT DEFAULT '',
            task_type TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col in ("subject TEXT DEFAULT ''", "task_type TEXT DEFAULT ''"):
        try:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
        except Exception:
            pass
    db.commit()
    db.close()


def extract_text_from_file(file) -> str:
    filename = file.filename.lower()
    content = file.read()

    if filename.endswith(".txt") or filename.endswith(".md"):
        return content.decode("utf-8", errors="ignore")

    if filename.endswith(".pdf"):
        try:
            import fitz
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            doc = fitz.open(tmp_path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            os.unlink(tmp_path)
            return text
        except Exception as e:
            return f"[Erro ao ler PDF: {e}]"

    if filename.endswith(".docx"):
        try:
            from docx import Document
            import io
            doc = Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return f"[Erro ao ler DOCX: {e}]"

    return content.decode("utf-8", errors="ignore")


def parse_tasks_with_groq(text: str) -> list[dict]:
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    today = datetime.now().strftime("%Y-%m-%d")

    # Split into chunks of ~12000 chars with 500 char overlap
    chunk_size = 12000
    overlap = 500
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap

    all_tasks = []
    seen = set()

    for chunk in chunks:
        prompt = f"""Analise o seguinte trecho de documento acadêmico/sistemática escolar e extraia as atividades de cada matéria.

Data de hoje: {today}

EXTRAIA APENAS estes tipos de atividade (ignore todo o resto, especialmente Recuperação):
- Tarefa Diária
- Trabalho Bimestral
- Simulado
- Avaliação

Para cada atividade encontrada, retorne um JSON com este formato exato:
{{
  "tasks": [
    {{
      "title": "título curto descrevendo a atividade",
      "description": "Objetivo do conhecimento: [objetivo da atividade]\\nOnde encontrar: [livro, página, capítulo ou recurso indicado]",
      "date": "YYYY-MM-DD",
      "time": null,
      "subject": "nome exato da matéria em português (Português, Matemática, Ciências, História, Geografia, Inglês, Artes, Ed. Física, Religião ou Outros)",
      "task_type": "Tarefa Diária, Trabalho Bimestral, Simulado ou Avaliação"
    }}
  ]
}}

Regras:
- Se não houver ano especificado, use {datetime.now().year}.
- NÃO inclua Recuperação nem nenhuma atividade de reforço/recuperação.
- A description DEVE ter os dois campos separados por quebra de linha.
- Se alguma informação não estiver no documento, escreva "Não informado".
- Se não houver nenhuma atividade válida neste trecho, retorne tasks como lista vazia.
- Responda APENAS com o JSON, sem texto adicional.

TRECHO DO DOCUMENTO:
{chunk}"""

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            data = json.loads(raw)
            chunk_tasks = data.get("tasks", [])

            for t in chunk_tasks:
                subject = t.get("subject", "Outros")
                t["color"] = SUBJECT_COLORS.get(subject, "#555555")
                key = (t.get("title", "").strip().lower(), t.get("date", ""), subject)
                if key not in seen:
                    seen.add(key)
                    all_tasks.append(t)
        except Exception:
            continue

    return all_tasks


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    db = get_db()
    tasks = db.execute("SELECT * FROM tasks ORDER BY date, time").fetchall()
    return jsonify([dict(t) for t in tasks])


@app.route("/api/tasks", methods=["POST"])
def create_task():
    data = request.json
    if not data or not data.get("title") or not data.get("date"):
        return jsonify({"error": "title e date são obrigatórios"}), 400

    subject = data.get("subject", "Outros")
    color = SUBJECT_COLORS.get(subject, data.get("color", "#555555"))

    db = get_db()
    cursor = db.execute(
        "INSERT INTO tasks (title, description, date, time, color, subject, task_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            data["title"],
            data.get("description", ""),
            data["date"],
            data.get("time"),
            color,
            subject,
            data.get("task_type", ""),
        ),
    )
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(task)), 201


@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    data = request.json
    db = get_db()
    existing = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Tarefa não encontrada"}), 404

    subject = data.get("subject", existing["subject"] or "Outros")
    color = SUBJECT_COLORS.get(subject, existing["color"])

    db.execute(
        "UPDATE tasks SET title=?, description=?, date=?, time=?, color=?, subject=?, task_type=? WHERE id=?",
        (
            data.get("title", existing["title"]),
            data.get("description", existing["description"]),
            data.get("date", existing["date"]),
            data.get("time", existing["time"]),
            color,
            subject,
            data.get("task_type", existing["task_type"] or ""),
            task_id,
        ),
    )
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return jsonify(dict(task))


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Arquivo inválido"}), 400

    allowed = {".txt", ".pdf", ".docx", ".md"}
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in allowed:
        return jsonify({"error": f"Tipo não suportado. Use: {', '.join(allowed)}"}), 400

    text = extract_text_from_file(file)
    if not text.strip():
        return jsonify({"error": "Não foi possível extrair texto do arquivo"}), 400

    if not os.environ.get("GROQ_API_KEY"):
        return jsonify({"error": "GROQ_API_KEY não configurada. Defina a variável de ambiente e reinicie o servidor."}), 500

    try:
        tasks = parse_tasks_with_groq(text)
    except Exception as e:
        return jsonify({"error": f"Erro ao processar com IA: {str(e)}"}), 500

    db = get_db()
    created = []
    for t in tasks:
        try:
            cursor = db.execute(
                "INSERT INTO tasks (title, description, date, time, color, subject, task_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    t["title"],
                    t.get("description", ""),
                    t["date"],
                    t.get("time"),
                    t.get("color", "#555555"),
                    t.get("subject", "Outros"),
                    t.get("task_type", ""),
                ),
            )
            task = db.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
            created.append(dict(task))
        except Exception:
            continue
    db.commit()

    return jsonify({"created": len(created), "tasks": created})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
