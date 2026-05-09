import os
import json
import sqlite3
import tempfile
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, g, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
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
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1,
            title TEXT NOT NULL,
            description TEXT,
            date TEXT NOT NULL,
            time TEXT,
            color TEXT DEFAULT '#555555',
            subject TEXT DEFAULT '',
            task_type TEXT DEFAULT '',
            completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col in (
        "subject TEXT DEFAULT ''",
        "task_type TEXT DEFAULT ''",
        "completed INTEGER DEFAULT 0",
        "user_id INTEGER NOT NULL DEFAULT 1",
    ):
        try:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
        except Exception:
            pass
    db.commit()
    db.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Não autenticado"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def current_user_id():
    return session["user_id"]


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


# ── Auth Routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET"])
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.json
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Usuário e senha são obrigatórios"}), 400

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Usuário ou senha incorretos"}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"ok": True, "username": user["username"]})


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.json
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Usuário e senha são obrigatórios"}), 400
    if len(username) < 3:
        return jsonify({"error": "Usuário deve ter pelo menos 3 caracteres"}), 400
    if len(password) < 6:
        return jsonify({"error": "Senha deve ter pelo menos 6 caracteres"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return jsonify({"error": "Nome de usuário já existe"}), 409

    password_hash = generate_password_hash(password)
    cursor = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    db.commit()

    session["user_id"] = cursor.lastrowid
    session["username"] = username
    return jsonify({"ok": True, "username": username}), 201


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    if "user_id" not in session:
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "username": session.get("username")})


# ── App Routes ───────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session.get("username"))


@app.route("/api/tasks", methods=["GET"])
@login_required
def get_tasks():
    db = get_db()
    tasks = db.execute(
        "SELECT * FROM tasks WHERE user_id=? ORDER BY date, time",
        (current_user_id(),)
    ).fetchall()
    return jsonify([dict(t) for t in tasks])


@app.route("/api/tasks", methods=["POST"])
@login_required
def create_task():
    data = request.json
    if not data or not data.get("title") or not data.get("date"):
        return jsonify({"error": "title e date são obrigatórios"}), 400

    subject = data.get("subject", "Outros")
    color = SUBJECT_COLORS.get(subject, data.get("color", "#555555"))

    db = get_db()
    cursor = db.execute(
        "INSERT INTO tasks (user_id, title, description, date, time, color, subject, task_type, completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (current_user_id(), data["title"], data.get("description", ""), data["date"],
         data.get("time"), color, subject, data.get("task_type", "")),
    )
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(task)), 201


@app.route("/api/tasks/bulk", methods=["POST"])
@login_required
def bulk_action():
    data = request.json
    action = data.get("action")
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "Nenhuma tarefa selecionada"}), 400

    uid = current_user_id()
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    params = ids + [uid]

    if action == "delete":
        db.execute(f"DELETE FROM tasks WHERE id IN ({placeholders}) AND user_id=?", params)
    elif action == "complete":
        db.execute(f"UPDATE tasks SET completed=1 WHERE id IN ({placeholders}) AND user_id=?", params)
    elif action == "uncomplete":
        db.execute(f"UPDATE tasks SET completed=0 WHERE id IN ({placeholders}) AND user_id=?", params)
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
@login_required
def update_task(task_id):
    data = request.json
    db = get_db()
    existing = db.execute(
        "SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, current_user_id())
    ).fetchone()
    if not existing:
        return jsonify({"error": "Tarefa não encontrada"}), 404

    subject = data.get("subject", existing["subject"] or "Outros")
    color = SUBJECT_COLORS.get(subject, existing["color"])

    db.execute(
        "UPDATE tasks SET title=?, description=?, date=?, time=?, color=?, subject=?, task_type=? WHERE id=? AND user_id=?",
        (data.get("title", existing["title"]), data.get("description", existing["description"]),
         data.get("date", existing["date"]), data.get("time", existing["time"]),
         color, subject, data.get("task_type", existing["task_type"] or ""),
         task_id, current_user_id()),
    )
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return jsonify(dict(task))


@app.route("/api/tasks/<int:task_id>/complete", methods=["PATCH"])
@login_required
def toggle_complete(task_id):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, current_user_id())
    ).fetchone()
    if not existing:
        return jsonify({"error": "Tarefa não encontrada"}), 404
    new_status = 0 if existing["completed"] else 1
    db.execute("UPDATE tasks SET completed=? WHERE id=? AND user_id=?",
               (new_status, task_id, current_user_id()))
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return jsonify(dict(task))


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, current_user_id()))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
@login_required
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
        return jsonify({"error": "GROQ_API_KEY não configurada."}), 500

    try:
        tasks = parse_tasks_with_groq(text)
    except Exception as e:
        return jsonify({"error": f"Erro ao processar com IA: {str(e)}"}), 500

    db = get_db()
    created = []
    for t in tasks:
        try:
            cursor = db.execute(
                "INSERT INTO tasks (user_id, title, description, date, time, color, subject, task_type, completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (current_user_id(), t["title"], t.get("description", ""), t["date"],
                 t.get("time"), t.get("color", "#555555"), t.get("subject", "Outros"), t.get("task_type", "")),
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
