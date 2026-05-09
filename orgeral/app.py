import os
import json
import sqlite3
import tempfile
from datetime import datetime
from flask import Flask, request, jsonify, render_template, g
import anthropic

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB

DB_PATH = os.path.join(os.path.dirname(__file__), "orgeral.db")


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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
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


def parse_tasks_with_claude(text: str) -> list[dict]:
    client = anthropic.Anthropic()
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""Analise o seguinte documento acadêmico/sistemática e extraia todas as tarefas, atividades, provas, trabalhos, datas de entrega e eventos importantes.

Data de hoje: {today}

Para cada item encontrado, retorne um JSON com este formato exato:
{{
  "tasks": [
    {{
      "title": "título curto da tarefa",
      "description": "descrição detalhada opcional",
      "date": "YYYY-MM-DD",
      "time": "HH:MM ou null",
      "color": "#555555 para tarefa normal, #222222 para prova/avaliação, #888888 para entrega"
    }}
  ]
}}

Se não houver ano especificado, use {datetime.now().year}. Se a data for vaga (ex: "próxima semana"), estime com base na data de hoje.
Se não encontrar nenhuma tarefa com data, retorne tasks como lista vazia.

Responda APENAS com o JSON, sem texto adicional.

DOCUMENTO:
{text[:8000]}"""

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    return data.get("tasks", [])


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

    db = get_db()
    cursor = db.execute(
        "INSERT INTO tasks (title, description, date, time, color) VALUES (?, ?, ?, ?, ?)",
        (
            data["title"],
            data.get("description", ""),
            data["date"],
            data.get("time"),
            data.get("color", "#555555"),
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

    db.execute(
        "UPDATE tasks SET title=?, description=?, date=?, time=?, color=? WHERE id=?",
        (
            data.get("title", existing["title"]),
            data.get("description", existing["description"]),
            data.get("date", existing["date"]),
            data.get("time", existing["time"]),
            data.get("color", existing["color"]),
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

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY não configurada. Defina a variável de ambiente e reinicie o servidor."}), 500

    try:
        tasks = parse_tasks_with_claude(text)
    except Exception as e:
        return jsonify({"error": f"Erro ao processar com IA: {str(e)}"}), 500

    db = get_db()
    created = []
    for t in tasks:
        try:
            cursor = db.execute(
                "INSERT INTO tasks (title, description, date, time, color) VALUES (?, ?, ?, ?, ?)",
                (t["title"], t.get("description", ""), t["date"], t.get("time"), t.get("color", "#555555")),
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
