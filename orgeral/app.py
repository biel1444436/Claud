import os
import json
import sqlite3
import tempfile
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, g, session, redirect, url_for
from authlib.integrations.flask_client import OAuth
from groq import Groq

# ── Secret key (persists across restarts) ────────────────────────────────────
_SECRET_KEY_FILE = os.path.join(os.path.dirname(__file__), ".secret_key")

def _load_secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    if os.path.exists(_SECRET_KEY_FILE):
        with open(_SECRET_KEY_FILE) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(_SECRET_KEY_FILE, "w") as f:
        f.write(key)
    return key

app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

DB_PATH = os.path.join(os.path.dirname(__file__), "orgeral.db")

# ── Google OAuth ──────────────────────────────────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

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
            google_id TEXT UNIQUE NOT NULL,
            email TEXT,
            name TEXT,
            picture TEXT,
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


# ── Auth Routes ───────────────────────────────────────────────────────────────

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        return "<h2 style='font-family:sans-serif;color:#e07070;padding:40px'>Configure GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nas variáveis de ambiente.</h2>", 500
    return render_template("login.html")


@app.route("/login/google")
def login_google():
    redirect_uri = url_for("callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/callback")
def callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo")

        google_id = user_info["sub"]
        email     = user_info.get("email", "")
        name      = user_info.get("name", email)
        picture   = user_info.get("picture", "")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()

        if user:
            db.execute(
                "UPDATE users SET email=?, name=?, picture=? WHERE google_id=?",
                (email, name, picture, google_id),
            )
            db.commit()
            user = db.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
        else:
            cursor = db.execute(
                "INSERT INTO users (google_id, email, name, picture) VALUES (?, ?, ?, ?)",
                (google_id, email, name, picture),
            )
            db.commit()
            user = db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()

        session["user_id"]  = user["id"]
        session["username"] = name
        session["picture"]  = picture
        return redirect(url_for("index"))

    except Exception as e:
        return redirect(url_for("login_page"))


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ── App Routes ────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        username=session.get("username"),
        picture=session.get("picture", ""),
    )


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
    color   = SUBJECT_COLORS.get(subject, data.get("color", "#555555"))

    db = get_db()
    cursor = db.execute(
        "INSERT INTO tasks (user_id, title, description, date, time, color, subject, task_type, completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (current_user_id(), data["title"], data.get("description", ""), data["date"],
         data.get("time"), color, subject, data.get("task_type", "")),
    )
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(task)), 201


@app.route("/api/tasks/bulk", methods=["POST"])
@login_required
def bulk_action():
    data   = request.json
    action = data.get("action")
    ids    = data.get("ids", [])
    if not ids:
        return jsonify({"error": "Nenhuma tarefa selecionada"}), 400

    uid = current_user_id()
    db  = get_db()
    ph  = ",".join("?" * len(ids))
    params = ids + [uid]

    if action == "delete":
        db.execute(f"DELETE FROM tasks WHERE id IN ({ph}) AND user_id=?", params)
    elif action == "complete":
        db.execute(f"UPDATE tasks SET completed=1 WHERE id IN ({ph}) AND user_id=?", params)
    elif action == "uncomplete":
        db.execute(f"UPDATE tasks SET completed=0 WHERE id IN ({ph}) AND user_id=?", params)
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
@login_required
def update_task(task_id):
    data = request.json
    db   = get_db()
    existing = db.execute(
        "SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, current_user_id())
    ).fetchone()
    if not existing:
        return jsonify({"error": "Tarefa não encontrada"}), 404

    subject = data.get("subject", existing["subject"] or "Outros")
    color   = SUBJECT_COLORS.get(subject, existing["color"])

    db.execute(
        "UPDATE tasks SET title=?, description=?, date=?, time=?, color=?, subject=?, task_type=? WHERE id=? AND user_id=?",
        (data.get("title", existing["title"]), data.get("description", existing["description"]),
         data.get("date", existing["date"]), data.get("time", existing["time"]),
         color, subject, data.get("task_type", existing["task_type"] or ""),
         task_id, current_user_id()),
    )
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
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
    task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
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
            task = db.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
            created.append(dict(task))
        except Exception:
            continue
    db.commit()
    return jsonify({"created": len(created), "tasks": created})


# ── File helpers ──────────────────────────────────────────────────────────────

def extract_text_from_file(file) -> str:
    filename = file.filename.lower()
    content  = file.read()

    if filename.endswith(".txt") or filename.endswith(".md"):
        return content.decode("utf-8", errors="ignore")

    if filename.endswith(".pdf"):
        try:
            import fitz
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content); tmp_path = tmp.name
            doc  = fitz.open(tmp_path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close(); os.unlink(tmp_path)
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
    today  = datetime.now().strftime("%Y-%m-%d")

    chunk_size, overlap = 12000, 500
    chunks, start = [], 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text): break
        start = end - overlap

    all_tasks, seen = [], set()

    for chunk in chunks:
        prompt = f"""Analise o seguinte trecho de documento acadêmico/sistemática escolar e extraia as atividades de cada matéria.

Data de hoje: {today}

EXTRAIA APENAS estes tipos (ignore Recuperação e reforço):
- Tarefa Diária  - Trabalho Bimestral  - Simulado  - Avaliação

Retorne APENAS este JSON:
{{
  "tasks": [{{
    "title": "título curto",
    "description": "Objetivo do conhecimento: [objetivo]\\nOnde encontrar: [recurso]",
    "date": "YYYY-MM-DD",
    "time": null,
    "subject": "Português|Matemática|Ciências|História|Geografia|Inglês|Artes|Ed. Física|Religião|Outros",
    "task_type": "Tarefa Diária|Trabalho Bimestral|Simulado|Avaliação"
  }}]
}}

Ano padrão: {datetime.now().year}. Se não houver tarefas válidas, retorne lista vazia.

TRECHO:
{chunk}"""

        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            raw = raw.strip()
            for t in json.loads(raw).get("tasks", []):
                subj = t.get("subject", "Outros")
                t["color"] = SUBJECT_COLORS.get(subj, "#555555")
                key = (t.get("title", "").strip().lower(), t.get("date", ""), subj)
                if key not in seen:
                    seen.add(key); all_tasks.append(t)
        except Exception:
            continue

    return all_tasks


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
