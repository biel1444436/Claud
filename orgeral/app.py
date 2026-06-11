import os
import json

# Carrega variáveis de um arquivo .env, se existir (conveniência p/ dev local).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import tempfile
import secrets
import logging
import time
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, request, jsonify, render_template, g, session, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from authlib.integrations.flask_client import OAuth
from groq import Groq

import gcal
import db as database

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("orgeral")

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

# Cookies de sessão seguros. SECURE só liga em produção (HTTPS).
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
)

# Atrás de proxy (Render/Railway/etc.) para url_for(_external=True) gerar HTTPS.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Escopo inclui calendar.events para espelhar tarefas no Google Agenda.
GOOGLE_SCOPE = "openid email profile https://www.googleapis.com/auth/calendar.events"

# Rate limit simples em memória para /api/upload (chama IA, tem custo).
_UPLOAD_HITS: dict[int, list[float]] = {}
UPLOAD_LIMIT  = int(os.environ.get("UPLOAD_LIMIT", "12"))
UPLOAD_WINDOW = int(os.environ.get("UPLOAD_WINDOW", "600"))  # segundos

# ── Google OAuth ──────────────────────────────────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": GOOGLE_SCOPE},
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
        g.db = database.connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
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


def current_user_row(db):
    return db.execute("SELECT * FROM users WHERE id=?", (current_user_id(),)).fetchone()


# ── CSRF: bloqueia requisições de origem cruzada em métodos de escrita ─────────
@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin:
            if urlparse(origin).netloc != request.host:
                log.warning("CSRF bloqueado: origin=%s host=%s", origin, request.host)
                return jsonify({"error": "Origem inválida"}), 403


# ── Sincronização com Google Agenda (best effort) ─────────────────────────────
def sync_task(db, user, task_row):
    """Espelha a tarefa no Google Agenda e salva o event_id. Nunca lança."""
    if not user or not user["refresh_token"]:
        return
    try:
        task = dict(task_row)
        eid = gcal.upsert_event(user, task, db)
        if eid and eid != task.get("gcal_event_id"):
            db.execute("UPDATE tasks SET gcal_event_id=? WHERE id=?", (eid, task["id"]))
            db.commit()
    except Exception as e:
        log.warning("Falha ao sincronizar tarefa %s na agenda: %s", task_row["id"], e)


def unsync_task(db, user, task_row):
    """Remove o evento da agenda. Nunca lança."""
    if not user or not user["refresh_token"] or not task_row["gcal_event_id"]:
        return
    try:
        gcal.delete_event(user, task_row["gcal_event_id"], db)
    except Exception as e:
        log.warning("Falha ao remover evento da agenda: %s", e)


# ── Auth Routes ───────────────────────────────────────────────────────────────

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        return "<h2 style='font-family:sans-serif;color:#e07070;padding:40px'>Configure GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nas variáveis de ambiente.</h2>", 500
    return render_template("login.html", error=request.args.get("error"))


@app.route("/login/google")
def login_google():
    redirect_uri = url_for("callback", _external=True)
    # access_type=offline + prompt=consent => garante refresh_token p/ a agenda.
    return google.authorize_redirect(redirect_uri, access_type="offline", prompt="consent")


@app.route("/callback")
def callback():
    try:
        token = google.authorize_access_token()
    except Exception as e:
        log.warning("OAuth falhou na troca de token: %s", e)
        return redirect(url_for("login_page", error="auth"))

    try:
        user_info = token.get("userinfo") or {}
        google_id = user_info["sub"]
        email     = user_info.get("email", "")
        name      = user_info.get("name", email)
        picture   = user_info.get("picture", "")

        access_token  = token.get("access_token")
        refresh_token = token.get("refresh_token")
        expires_at    = token.get("expires_at") or (time.time() + token.get("expires_in", 3600))

        db = get_db()
        existing = db.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()

        if existing:
            # Google só manda refresh_token na 1ª autorização; preserva o antigo.
            new_refresh = refresh_token or existing["refresh_token"]
            db.execute(
                """UPDATE users SET email=?, name=?, picture=?,
                   access_token=?, refresh_token=?, token_expiry=? WHERE google_id=?""",
                (email, name, picture, access_token, new_refresh, expires_at, google_id),
            )
            db.commit()
            user = db.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
        else:
            cursor = db.execute(
                """INSERT INTO users (google_id, email, name, picture,
                   access_token, refresh_token, token_expiry) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (google_id, email, name, picture, access_token, refresh_token, expires_at),
            )
            db.commit()
            user = db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()

        session["user_id"]  = user["id"]
        session["username"] = name
        session["picture"]  = picture
        return redirect(url_for("index"))

    except Exception as e:
        log.exception("Erro no callback do OAuth: %s", e)
        return redirect(url_for("login_page", error="callback"))


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ── App Routes ────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    db = get_db()
    user = current_user_row(db)
    return render_template(
        "index.html",
        username=session.get("username"),
        picture=session.get("picture", ""),
        calendar_connected=bool(user and user["refresh_token"]),
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
    sync_task(db, current_user_row(db), task)
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
    user = current_user_row(db)
    ph  = ",".join("?" * len(ids))
    params = ids + [uid]

    affected = db.execute(
        f"SELECT * FROM tasks WHERE id IN ({ph}) AND user_id=?", params
    ).fetchall()

    if action == "delete":
        for t in affected:
            unsync_task(db, user, t)
        db.execute(f"DELETE FROM tasks WHERE id IN ({ph}) AND user_id=?", params)
        db.commit()
    elif action in ("complete", "uncomplete"):
        new_val = 1 if action == "complete" else 0
        db.execute(f"UPDATE tasks SET completed=? WHERE id IN ({ph}) AND user_id=?",
                   [new_val] + params)
        db.commit()
        for t in db.execute(f"SELECT * FROM tasks WHERE id IN ({ph}) AND user_id=?", params).fetchall():
            sync_task(db, user, t)
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
    sync_task(db, current_user_row(db), task)
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
    sync_task(db, current_user_row(db), task)
    task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return jsonify(dict(task))


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    db = get_db()
    task = db.execute(
        "SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, current_user_id())
    ).fetchone()
    if task:
        unsync_task(db, current_user_row(db), task)
    db.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, current_user_id()))
    db.commit()
    return jsonify({"ok": True})


# ── Google Agenda ─────────────────────────────────────────────────────────────

@app.route("/api/calendar/status", methods=["GET"])
@login_required
def calendar_status():
    db = get_db()
    user = current_user_row(db)
    return jsonify({
        "configured": gcal.calendar_configured(),
        "connected": bool(user and user["refresh_token"]),
    })


@app.route("/api/calendar/sync-all", methods=["POST"])
@login_required
def calendar_sync_all():
    db = get_db()
    user = current_user_row(db)
    if not user or not user["refresh_token"]:
        return jsonify({"error": "Conecte sua conta Google novamente para liberar a agenda."}), 400

    tasks = db.execute("SELECT * FROM tasks WHERE user_id=?", (current_user_id(),)).fetchall()
    synced, failed = 0, 0
    for t in tasks:
        try:
            eid = gcal.upsert_event(user, dict(t), db)
            if eid and eid != t["gcal_event_id"]:
                db.execute("UPDATE tasks SET gcal_event_id=? WHERE id=?", (eid, t["id"]))
                db.commit()
            synced += 1
        except Exception as e:
            log.warning("sync-all falhou na tarefa %s: %s", t["id"], e)
            failed += 1
    return jsonify({"synced": synced, "failed": failed, "total": len(tasks)})


@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    # Rate limit por usuário.
    uid = current_user_id()
    now = time.time()
    hits = [t for t in _UPLOAD_HITS.get(uid, []) if now - t < UPLOAD_WINDOW]
    if len(hits) >= UPLOAD_LIMIT:
        return jsonify({"error": "Muitos uploads em pouco tempo. Tente novamente em alguns minutos."}), 429
    hits.append(now)
    _UPLOAD_HITS[uid] = hits

    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Arquivo inválido"}), 400

    allowed = {".txt", ".pdf", ".docx", ".md"}
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in allowed:
        return jsonify({"error": f"Tipo não suportado. Use: {', '.join(allowed)}"}), 400

    try:
        text = extract_text_from_file(file)
    except Exception as e:
        log.warning("Falha ao extrair texto de %s: %s", file.filename, e)
        return jsonify({"error": f"Não foi possível ler o arquivo: {e}"}), 400

    if not text.strip():
        return jsonify({"error": "Não foi possível extrair texto do arquivo"}), 400
    if not os.environ.get("GROQ_API_KEY"):
        return jsonify({"error": "GROQ_API_KEY não configurada."}), 500

    try:
        tasks = parse_tasks_with_groq(text)
    except Exception as e:
        log.exception("Erro ao processar com IA: %s", e)
        return jsonify({"error": f"Erro ao processar com IA: {str(e)}"}), 500

    db = get_db()
    user = current_user_row(db)
    created = []
    for t in tasks:
        try:
            cursor = db.execute(
                "INSERT INTO tasks (user_id, title, description, date, time, color, subject, task_type, completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (uid, t["title"], t.get("description", ""), t["date"],
                 t.get("time"), t.get("color", "#555555"), t.get("subject", "Outros"), t.get("task_type", "")),
            )
            task = db.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
            sync_task(db, user, task)
            task = db.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone()
            created.append(dict(task))
        except Exception as e:
            log.warning("Falha ao inserir tarefa do upload (%s): %s", t.get("title"), e)
            continue
    db.commit()
    return jsonify({"created": len(created), "tasks": created})


# ── File helpers ──────────────────────────────────────────────────────────────

def extract_text_from_file(file) -> str:
    """Extrai texto do arquivo. Lança exceção em caso de erro de parsing."""
    filename = file.filename.lower()
    content  = file.read()

    if filename.endswith(".txt") or filename.endswith(".md"):
        return content.decode("utf-8", errors="ignore")

    if filename.endswith(".pdf"):
        import fitz
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content); tmp_path = tmp.name
            doc  = fitz.open(tmp_path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            return text
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if filename.endswith(".docx"):
        from docx import Document
        import io
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)

    return content.decode("utf-8", errors="ignore")


def _groq_complete(client, prompt: str, max_retries: int = 6) -> str:
    """Chama a Groq com retry/backoff em caso de 429 (rate limit)."""
    delay = 4.0
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"},
            )
            return resp.choices[0].message.content
        except Exception as e:
            last_err = e
            status = getattr(e, "status_code", None)
            is_rate = status == 429 or "429" in str(e) or "rate" in str(e).lower()
            if not (is_rate and attempt < max_retries - 1):
                raise
            # Respeita o Retry-After do cabeçalho, se houver.
            wait = delay
            resp_obj = getattr(e, "response", None)
            if resp_obj is not None:
                try:
                    wait = float(resp_obj.headers.get("retry-after")) or delay
                except (TypeError, ValueError):
                    pass
            log.info("Groq 429 — aguardando %.1fs (tentativa %d/%d)", wait, attempt + 1, max_retries)
            time.sleep(wait)
            delay = min(delay * 2, 30)
    raise last_err if last_err else RuntimeError("Groq: limite de tentativas excedido")


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

    for i, chunk in enumerate(chunks):
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
            raw = _groq_complete(client, prompt)
            for t in json.loads(raw).get("tasks", []):
                subj = t.get("subject", "Outros")
                t["color"] = SUBJECT_COLORS.get(subj, "#555555")
                key = (t.get("title", "").strip().lower(), t.get("date", ""), subj)
                if key not in seen:
                    seen.add(key); all_tasks.append(t)
        except Exception as e:
            log.warning("Chunk %d/%d ignorado no parsing da IA: %s", i + 1, len(chunks), e)
            continue

        # Pausa entre os pedaços para não estourar o rate limit da Groq.
        if i < len(chunks) - 1:
            time.sleep(1.0)

    log.info("Parsing concluído: %d tarefas de %d pedaço(s)", len(all_tasks), len(chunks))
    return all_tasks


# Garante o schema também sob gunicorn (sem bloco __main__).
database.init_db()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port  = int(os.environ.get("PORT", "5000"))
    app.run(debug=debug, host="0.0.0.0", port=port)
