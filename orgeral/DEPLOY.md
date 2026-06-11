# 🚀 Deploy do Orgeral

Guia passo a passo para colocar o Orgeral no ar com login Google + Google Agenda.

---

## 1. Pré-requisitos (chaves)

Você vai precisar de 3 segredos:

| Variável | Onde pegar |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console (passo 2) |
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `SECRET_KEY` | gere com `python -c "import secrets;print(secrets.token_hex(32))"` (o Render gera sozinho) |

---

## 2. Configurar o Google (OAuth + Calendar)

1. Acesse https://console.cloud.google.com/ e crie/escolha um projeto.
2. **APIs & Services → Library** → habilite **Google Calendar API**.
3. **APIs & Services → OAuth consent screen**:
   - Tipo **External**, preencha nome/email.
   - Em **Scopes**, adicione: `.../auth/userinfo.email`, `.../auth/userinfo.profile`,
     `openid` e **`.../auth/calendar.events`**.
   - Em **Test users**, adicione seu e-mail (enquanto o app estiver em "Testing").
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Tipo **Web application**.
   - **Authorized redirect URIs** — adicione os dois:
     - `http://localhost:5000/callback` (testes locais)
     - `https://SEU-DOMINIO/callback` (produção — ex.: `https://orgeral.onrender.com/callback`)
   - Copie o **Client ID** e **Client secret**.

> ⚠️ O redirect URI tem que bater **exatamente** com a URL do app (incluindo `https`).

---

## 3. Rodar localmente (opcional, para testar)

```powershell
cd orgeral
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Copie .env.example para .env e preencha as chaves
# Depois carregue as variáveis e rode:
$env:GOOGLE_CLIENT_ID="..."; $env:GOOGLE_CLIENT_SECRET="..."; $env:GROQ_API_KEY="..."
python app.py
```

Abra http://localhost:5000

---

## 4. Subir no Render (recomendado)

O Render é grátis para começar e já vem com HTTPS.

1. Suba a pasta `orgeral` para um repositório no **GitHub**.
   (O `.gitignore` já evita versionar `.secret_key`, `.env` e o banco.)
2. No Render: **New → Blueprint** e aponte para o repo (ele lê o `render.yaml`).
   - Ou **New → Web Service** manual com:
     - Build: `pip install -r requirements.txt`
     - Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
3. Em **Environment**, cadastre os segredos:
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GROQ_API_KEY`.
   (`SECRET_KEY`, `DB_PATH`, `SESSION_COOKIE_SECURE` etc. já vêm do `render.yaml`.)
4. Deploy. Anote a URL final (ex.: `https://orgeral.onrender.com`).
5. **Volte ao Google Cloud** e adicione `https://orgeral.onrender.com/callback`
   em Authorized redirect URIs.

> 💾 **Persistência:** o plano `free` apaga o disco a cada deploy (perde o banco).
> O `render.yaml` usa o plano `starter` com disco em `/var/data` para o banco sobreviver.
> Para escala real, troque o SQLite por Postgres.

---

## 5. Alternativas de host

- **Railway** / **Heroku**: usam o `Procfile` direto. Cadastre as mesmas env vars
  e adicione o redirect URI correspondente no Google.
- **Fly.io**: precisa de um `fly.toml` + volume; me avise que eu gero.

---

## 6. Conectar a Google Agenda (uso)

1. Faça login no app com o Google.
2. Na 1ª vez o Google pede permissão de **Calendar** — aceite.
3. Toda tarefa nova/editada/concluída vira evento na sua agenda automaticamente.
4. Tarefas que já existiam antes de conectar: clique em
   **"↻ Sincronizar tarefas existentes"** na barra lateral.

As cores das matérias são mapeadas para as cores do Google Agenda.
