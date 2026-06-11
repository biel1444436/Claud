"""Integração com o Google Agenda (Google Calendar API).

Cada tarefa do Orgeral pode ser espelhada como um evento na agenda
"primary" do usuário. O id do evento é guardado em tasks.gcal_event_id
para permitir update/delete posteriores.

A sincronização é sempre "best effort": qualquer falha é registrada em log
e nunca derruba a operação principal (criar/editar/concluir tarefa).
"""
import os
import time
import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger("orgeral.gcal")

TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_API   = "https://www.googleapis.com/calendar/v3"
TIMEZONE  = os.environ.get("CAL_TIMEZONE", "America/Sao_Paulo")

# Mapa matéria -> colorId do Google Calendar (aproximação das cores do app)
SUBJECT_COLOR_IDS = {
    "Português":  "10",  # Basil  (verde)
    "Matemática": "9",   # Blueberry (azul)
    "Ciências":   "3",   # Grape  (roxo)
    "História":   "11",  # Tomato (vermelho)
    "Geografia":  "6",   # Tangerine (laranja)
    "Inglês":     "2",   # Sage   (verde-água)
    "Artes":      "1",   # Lavender
    "Ed. Física": "4",   # Flamingo
    "Religião":   "5",   # Banana (amarelo)
    "Outros":     "8",   # Graphite (cinza)
}


def calendar_configured() -> bool:
    """True se as credenciais OAuth do Google estão configuradas."""
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _valid_access_token(user, db) -> str | None:
    """Devolve um access_token válido, renovando via refresh_token se preciso.

    Persiste o token renovado no banco. Retorna None se não há como autenticar.
    """
    refresh = user["refresh_token"]
    access  = user["access_token"]
    expiry  = user["token_expiry"] or 0

    if not refresh:
        return None

    # Ainda válido (com 60s de folga)?
    if access and time.time() < float(expiry) - 60:
        return access

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id":     os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": refresh,
            "grant_type":    "refresh_token",
        },
        timeout=10,
    )
    resp.raise_for_status()
    tok = resp.json()
    access = tok["access_token"]
    new_expiry = time.time() + tok.get("expires_in", 3600)
    db.execute(
        "UPDATE users SET access_token=?, token_expiry=? WHERE id=?",
        (access, new_expiry, user["id"]),
    )
    db.commit()
    return access


def _next_day(date_str: str) -> str:
    d = datetime.fromisoformat(date_str) + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _event_body(task: dict) -> dict:
    subject = task.get("subject") or ""
    done    = bool(task.get("completed"))
    prefix  = "✓ " if done else ""
    summary = prefix + (f"[{subject}] {task['title']}" if subject else task["title"])

    body = {
        "summary":     summary,
        "description": task.get("description") or "",
        "colorId":     SUBJECT_COLOR_IDS.get(subject, "8"),
        # Fonte para identificar eventos criados pelo Orgeral
        "source":      {"title": "Orgeral", "url": "https://orgeral.app"},
    }

    time_str = task.get("time")
    if time_str:
        start_dt = datetime.fromisoformat(f"{task['date']}T{time_str}")
        end_dt   = start_dt + timedelta(hours=1)
        body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE}
        body["end"]   = {"dateTime": end_dt.isoformat(),   "timeZone": TIMEZONE}
    else:
        body["start"] = {"date": task["date"]}
        body["end"]   = {"date": _next_day(task["date"])}

    return body


def upsert_event(user, task: dict, db) -> str | None:
    """Cria ou atualiza o evento da tarefa na agenda. Retorna o event_id.

    Lança exceção em erro de rede/API — quem chama deve tratar (best effort).
    """
    token = _valid_access_token(user, db)
    if not token:
        return task.get("gcal_event_id")

    headers = {"Authorization": f"Bearer {token}"}
    body    = _event_body(task)
    eid     = task.get("gcal_event_id")

    if eid:
        r = requests.put(
            f"{CAL_API}/calendars/primary/events/{eid}",
            json=body, headers=headers, timeout=10,
        )
        if r.status_code == 404:
            eid = None  # evento sumiu — recria abaixo
        else:
            r.raise_for_status()
            return eid

    r = requests.post(
        f"{CAL_API}/calendars/primary/events",
        json=body, headers=headers, timeout=10,
    )
    r.raise_for_status()
    return r.json()["id"]


def delete_event(user, event_id: str, db) -> None:
    """Remove o evento da agenda. Silencioso se já não existir."""
    if not event_id:
        return
    token = _valid_access_token(user, db)
    if not token:
        return
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.delete(
        f"{CAL_API}/calendars/primary/events/{event_id}",
        headers=headers, timeout=10,
    )
    # 404/410 = já removido; tudo bem
    if r.status_code not in (200, 204, 404, 410):
        r.raise_for_status()
