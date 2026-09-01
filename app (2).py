import os
import io
import json
import time
import base64
import secrets
import threading
import hashlib
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import quote

import qrcode
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import (
    Flask, request, redirect, url_for, session, render_template_string,
    jsonify, send_file, abort
)

app = Flask(__name__)

ADMIN_PIN = os.environ.get("ADMIN_PIN", "")
DATA_KEY = os.environ.get("JCRC_MASTER_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_DATA_TOKEN", "")
app.secret_key = hashlib.sha256(DATA_KEY.encode("utf-8")).hexdigest() if DATA_KEY else "CHANGE-ME"
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12
GITHUB_REPO = os.environ.get(
    "GITHUB_REPO", "secretariajockeyclub-glitch/vouchers-pileta-jcrc"
)
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "jcrc_vouchers_2026")

_state_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_ts():
    return int(time.time())


def state_url():
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_PATH}"


def gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Vouchers-Pileta-JCRC",
    }


def read_local_state():
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f), None


def load_state():
    if not GITHUB_TOKEN:
        return read_local_state()
    r = requests.get(
        state_url(),
        headers=gh_headers(),
        params={"ref": GITHUB_BRANCH},
        timeout=15,
    )
    r.raise_for_status()
    obj = r.json()
    raw = base64.b64decode(obj["content"]).decode("utf-8")
    return json.loads(raw), obj["sha"]


def save_state(state, sha=None):
    data = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    if not GITHUB_TOKEN:
        with open(STATE_PATH, "wb") as f:
            f.write(data)
        return

    if sha is None:
        _, sha = load_state()

    payload = {
        "message": "Actualiza estado de vouchers [skip render]",
        "content": base64.b64encode(data).decode("ascii"),
        "branch": GITHUB_BRANCH,
        "sha": sha,
    }
    r = requests.put(
        state_url(),
        headers=gh_headers(),
        json=payload,
        timeout=20,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub state save failed: {r.status_code} {r.text[:300]}")


def update_state(mutator):
    with _state_lock:
        last_error = None
        for _ in range(3):
            try:
                state, sha = load_state()
                result = mutator(state)
                save_state(state, sha)
                return result
            except Exception as e:
                last_error = e
                time.sleep(0.5)
        raise last_error


def fernet():
    if not DATA_KEY:
        raise RuntimeError("Falta JCRC_MASTER_KEY en Render.")
    return Fernet(DATA_KEY.encode("utf-8"))


def decrypt_member(member):
    try:
        raw = fernet().decrypt(member["payload"].encode("utf-8"))
        return json.loads(raw.decode("utf-8"))
    except (InvalidToken, KeyError) as e:
        raise RuntimeError("No se pudo leer la ficha. Revisá DATA_ENCRYPTION_KEY.") from e


def encrypt_payload(payload):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return fernet().encrypt(raw).decode("utf-8")


def normalize_phone(value):
    d = "".join(ch for ch in str(value or "") if ch.isdigit())
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("54"):
        rest = d[2:].lstrip("0")
        if rest.startswith("9"):
            return "54" + rest
        return "549" + rest
    d = d.lstrip("0")
    if len(d) == 9 and d.startswith("15"):
        d = "358" + d[2:]
    if len(d) == 10:
        return "549" + d
    return ""


def public_base():
    return BASE_URL or request.url_root.rstrip("/")


def is_admin():
    return bool(session.get("admin"))


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            session["next"] = request.url
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


BASE_CSS = """
:root{--orange:#f47b20;--black:#171717;--soft:#f4f4f4;--green:#198754;--red:#b42318}
*{box-sizing:border-box} body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f5f5f5;color:#171717}
.top{background:#171717;color:white;padding:14px 18px;border-bottom:5px solid #f47b20}
.top b{font-size:20px}.top a{color:white;margin-left:18px;text-decoration:none}
.wrap{max-width:1050px;margin:22px auto;padding:0 14px}
.card{background:white;border-radius:14px;padding:20px;box-shadow:0 2px 12px rgba(0,0,0,.08);margin-bottom:16px}
h1,h2,h3{margin-top:0}.muted{color:#666}.big{font-size:34px;font-weight:800}
.saldo{display:inline-block;background:#fff3e9;border:2px solid var(--orange);padding:9px 15px;border-radius:12px;font-weight:800}
.btn{display:inline-block;border:0;border-radius:10px;padding:12px 16px;font-size:16px;font-weight:700;cursor:pointer;text-decoration:none}
.btn-orange{background:var(--orange);color:white}.btn-black{background:#171717;color:white}.btn-green{background:var(--green);color:white}
.btn-gray{background:#e8e8e8;color:#111}.btn-red{background:var(--red);color:white}
input,select{width:100%;padding:11px;border:1px solid #bbb;border-radius:9px;font-size:16px}
label{font-weight:700;display:block;margin:10px 0 6px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}
table{width:100%;border-collapse:collapse;background:white} th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}
th{background:#171717;color:#fff;position:sticky;top:0}.ok{color:var(--green);font-weight:800}.bad{color:var(--red);font-weight:800}
.notice{padding:12px;border-radius:9px;background:#fff3e9;border-left:5px solid var(--orange);margin:12px 0}
.center{text-align:center}.status{font-size:38px;font-weight:900}.status.ok{color:var(--green)}
@media(max-width:700px){table{font-size:13px}.hide-mobile{display:none}.big{font-size:28px}}
"""


def page(title, body, script=""):
    nav = ""
    if is_admin():
        nav = """<a href="/admin">Inicio</a><a href="/admin/qrs">QR para imprimir</a><a href="/admin/history">Historial</a><a href="/logout">Salir</a>"""
    return render_template_string(
        """<!doctype html><html lang="es"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <meta name="robots" content="noindex,nofollow">
        <title>{{title}}</title><style>{{css}}</style></head>
        <body><div class="top"><b>JCRC · Vouchers Pileta</b>{{nav|safe}}</div>
        <div class="wrap">{{body|safe}}</div>{{script|safe}}</body></html>""",
        title=title, css=BASE_CSS, nav=nav, body=body, script=script
    )


@app.get("/")
def home():
    return redirect(url_for("admin_home") if is_admin() else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        pin = request.form.get("pin", "")
        if ADMIN_PIN and secrets.compare_digest(pin, ADMIN_PIN):
            session.clear()
            session["admin"] = True
            session.permanent = True
            nxt = request.form.get("next") or "/admin"
            return redirect(nxt if nxt.startswith("/") or nxt.startswith(public_base()) else "/admin")
        error = '<div class="notice bad">PIN incorrecto.</div>'
    nxt = session.get("next", "/admin")
    body = f"""
    <div class="card" style="max-width:430px;margin:50px auto">
      <h1>Ingreso recepción</h1>
      <p class="muted">Sistema de vouchers · Temporada 2026/27</p>
      {error}
      <form method="post">
        <input type="hidden" name="next" value="{nxt}">
        <label>PIN de recepción</label><input type="password" name="pin" inputmode="numeric" autofocus required>
        <br><br><button class="btn btn-orange" style="width:100%">Ingresar</button>
      </form>
    </div>"""
    return page("Ingreso", body)


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/admin")
@admin_required
def admin_home():
    try:
        state, _ = load_state()
        items = []
        q = request.args.get("q", "").strip().lower()
        for mid, member in state.get("members", {}).items():
            if not member.get("activo", True):
                continue
            p = decrypt_member(member)
            hay = f"{p.get('nombre','')} {p.get('socio','')}".lower()
            if q and q not in hay:
                continue
            items.append((p.get("nombre",""), mid, member, p))
        items.sort(key=lambda x: x[0].lower())
        rows = []
        for _, mid, m, p in items:
            phone = p.get("telefono_wa") or ""
            phone_html = '<span class="ok">OK</span>' if phone else '<span class="bad">FALTA</span>'
            rows.append(f"""
              <tr>
                <td><b>{p.get('nombre','')}</b><br><span class="muted">Socio {p.get('socio','')}</span></td>
                <td>{p.get('categoria','')}</td>
                <td><b>{m.get('saldo',0)}</b> / {m.get('invitaciones_iniciales',0)}</td>
                <td>{phone_html}</td>
                <td><a class="btn btn-orange" href="/v/{mid}">Abrir</a>
                    <a class="btn btn-gray" href="/qr/{mid}.png?download=1">QR</a></td>
              </tr>""")
        body = f"""
        <div class="card">
          <h1>Recepción · Vouchers</h1>
          <form method="get"><div style="display:flex;gap:8px">
            <input name="q" value="{request.args.get('q','')}" placeholder="Buscar por titular o Nº de socio">
            <button class="btn btn-black">Buscar</button>
          </div></form>
        </div>
        <div class="card" style="overflow:auto">
          <table><thead><tr><th>Titular</th><th>Categoría</th><th>Saldo</th><th>Tel.</th><th>Acción</th></tr></thead>
          <tbody>{''.join(rows)}</tbody></table>
        </div>"""
        if not GITHUB_TOKEN:
            body = '<div class="notice bad">Falta GITHUB_DATA_TOKEN: los cambios no serán persistentes.</div>' + body
        return page("Recepción", body)
    except Exception as e:
        return page("Error", f'<div class="card"><h2>Error</h2><p class="bad">{e}</p></div>'), 500


@app.route("/v/<mid>", methods=["GET"])
@admin_required
def voucher(mid):
    state, _ = load_state()
    m = state.get("members", {}).get(mid)
    if not m:
        abort(404)
    p = decrypt_member(m)
    saldo = int(m.get("saldo", 0))
    phone = p.get("telefono_wa") or ""
    phone_note = f'<span class="ok">WhatsApp: +{phone}</span>' if phone else '<span class="bad">Falta teléfono válido</span>'
    qty_options = "".join(f'<option value="{i}">{i}</option>' for i in range(1, max(saldo, 0)+1))
    form = ""
    if saldo > 0 and phone:
        form = f"""
        <form method="post" action="/v/{mid}/request">
          <label>Personas que quieren ingresar</label>
          <select name="qty">{qty_options}</select><br><br>
          <button class="btn btn-orange" style="width:100%">Crear solicitud de autorización</button>
        </form>"""
    elif saldo <= 0:
        form = '<div class="notice bad">Sin invitaciones disponibles.</div>'
    else:
        form = '<div class="notice">Cargá un teléfono para poder abrir WhatsApp.</div>'
    body = f"""
    <div class="card">
      <a href="/admin">← Volver</a><br><br>
      <div class="grid">
        <div><div class="muted">Titular</div><div class="big">{p.get('nombre','')}</div>
          <p><b>Nº socio:</b> {p.get('socio','')}<br><b>Categoría:</b> {p.get('categoria','')}<br>{phone_note}</p></div>
        <div class="center"><div class="muted">Saldo disponible</div><div class="big">{saldo}</div>
          <span class="saldo">de {m.get('invitaciones_iniciales',0)} invitaciones</span></div>
      </div>
      <hr style="border:0;border-top:1px solid #ddd;margin:20px 0">
      {form}
    </div>
    <div class="card">
      <h3>Editar teléfono / saldo</h3>
      <form method="post" action="/admin/member/{mid}/edit">
        <div class="grid">
          <div><label>Teléfono</label><input name="phone" value="{p.get('telefono_original','')}" placeholder="Ej. 358 4012345"></div>
          <div><label>Saldo actual</label><input name="saldo" type="number" min="0" value="{saldo}"></div>
        </div><br><button class="btn btn-gray">Guardar corrección</button>
      </form>
    </div>"""
    return page(p.get("nombre","Voucher"), body)


@app.post("/admin/member/<mid>/edit")
@admin_required
def edit_member(mid):
    phone_input = request.form.get("phone", "").strip()
    saldo_input = request.form.get("saldo", "").strip()

    def mutate(state):
        m = state["members"].get(mid)
        if not m:
            raise ValueError("Titular inexistente")
        p = decrypt_member(m)
        p["telefono_original"] = phone_input
        p["telefono_wa"] = normalize_phone(phone_input)
        m["payload"] = encrypt_payload(p)
        if saldo_input != "":
            m["saldo"] = max(0, int(saldo_input))
        state.setdefault("history", []).append({
            "at": now_iso(), "type": "admin_edit", "member_id": mid,
            "saldo": m["saldo"]
        })
        state["history"] = state["history"][-500:]
    update_state(mutate)
    return redirect(f"/v/{mid}")


@app.post("/v/<mid>/request")
@admin_required
def create_request(mid):
    qty = int(request.form.get("qty", "0"))
    token = secrets.token_urlsafe(24)

    def mutate(state):
        m = state.get("members", {}).get(mid)
        if not m:
            raise ValueError("Titular inexistente")
        saldo = int(m.get("saldo", 0))
        if qty < 1 or qty > saldo:
            raise ValueError("Cantidad inválida o saldo insuficiente")
        p = decrypt_member(m)
        if not p.get("telefono_wa"):
            raise ValueError("Falta teléfono válido")
        state.setdefault("requests", {})[token] = {
            "member_id": mid,
            "qty": qty,
            "status": "pending",
            "created_at": now_iso(),
            "created_ts": now_ts(),
        }
        return p, saldo

    p, saldo = update_state(mutate)
    auth_url = f"{public_base()}/a/{token}"
    msg = (
        f"Jockey Club Río Cuarto - Autorización de ingreso\n\n"
        f"Titular: {p.get('nombre','')}\n"
        f"Nº socio: {p.get('socio','')}\n"
        f"Se solicita autorizar el ingreso de {qty} persona{'s' if qty != 1 else ''}.\n\n"
        f"Para AUTORIZAR o RECHAZAR, abrí este enlace:\n{auth_url}\n\n"
        f"Esta solicitud vence en 30 minutos."
    )
    wa_url = f"https://wa.me/{p['telefono_wa']}?text={quote(msg)}"
    body = f"""
    <div class="card center">
      <h1>Solicitud creada</h1>
      <p><b>{p.get('nombre','')}</b> · {qty} persona{'s' if qty != 1 else ''}</p>
      <a class="btn btn-green" href="{wa_url}" target="_blank" rel="noopener">Abrir WhatsApp y enviar</a>
      <p class="muted">Después de enviar, dejá esta pantalla abierta. Se actualizará sola.</p>
      <div id="st" class="status">ESPERANDO...</div>
      <p id="detail">Saldo actual: {saldo}</p>
      <a class="btn btn-gray" href="/v/{mid}">Volver al voucher</a>
    </div>"""
    script = f"""<script>
      const poll=()=>fetch('/api/request/{token}').then(r=>r.json()).then(x=>{{
        const el=document.getElementById('st'), d=document.getElementById('detail');
        if(x.status==='approved'){{el.textContent='AUTORIZADO';el.className='status ok';d.textContent='Nuevo saldo: '+x.saldo;}}
        else if(x.status==='rejected'){{el.textContent='RECHAZADO';el.className='status bad';}}
        else if(x.status==='expired'){{el.textContent='VENCIDO';el.className='status bad';}}
        else setTimeout(poll,2500);
      }}).catch(()=>setTimeout(poll,4000)); poll();
    </script>"""
    return page("Solicitud", body, script)


@app.get("/api/request/<token>")
@admin_required
def request_status(token):
    state, _ = load_state()
    req = state.get("requests", {}).get(token)
    if not req:
        return jsonify({"status": "missing"}), 404
    status = req.get("status", "pending")
    if status == "pending" and now_ts() - int(req.get("created_ts", 0)) > 1800:
        status = "expired"
    m = state.get("members", {}).get(req["member_id"], {})
    return jsonify({"status": status, "saldo": m.get("saldo", 0)})


@app.route("/a/<token>", methods=["GET"])
def authorize_page(token):
    state, _ = load_state()
    req = state.get("requests", {}).get(token)
    if not req:
        return page("Solicitud inexistente", '<div class="card center"><h2>Solicitud inexistente</h2></div>'), 404
    m = state.get("members", {}).get(req["member_id"])
    if not m:
        abort(404)
    p = decrypt_member(m)
    status = req.get("status", "pending")
    expired = now_ts() - int(req.get("created_ts", 0)) > 1800
    if status == "pending" and expired:
        status = "expired"

    if status == "approved":
        body = f'<div class="card center"><div class="status ok">AUTORIZADO</div><p>{req["qty"]} persona(s). Gracias.</p></div>'
    elif status == "rejected":
        body = '<div class="card center"><div class="status bad">RECHAZADO</div><p>La solicitud ya fue rechazada.</p></div>'
    elif status == "expired":
        body = '<div class="card center"><div class="status bad">VENCIDO</div><p>Pedí a recepción una nueva solicitud.</p></div>'
    else:
        body = f"""
        <div class="card center" style="max-width:560px;margin:35px auto">
          <h2>Jockey Club Río Cuarto</h2>
          <p>Hola <b>{p.get('nombre','')}</b>.</p>
          <p>Recepción solicita autorizar el ingreso de:</p>
          <div class="big">{req['qty']} persona{'s' if req['qty'] != 1 else ''}</div>
          <p>Saldo actual: <b>{m.get('saldo',0)}</b></p>
          <form method="post" action="/a/{token}/approve">
            <button class="btn btn-green" style="width:100%;font-size:20px">AUTORIZAR INGRESO</button>
          </form><br>
          <form method="post" action="/a/{token}/reject">
            <button class="btn btn-red" style="width:100%">RECHAZAR</button>
          </form>
          <p class="muted">La autorización se puede usar una sola vez.</p>
        </div>"""
    return page("Autorizar ingreso", body)


@app.post("/a/<token>/approve")
def approve(token):
    def mutate(state):
        req = state.get("requests", {}).get(token)
        if not req:
            return "missing", None
        if req.get("status") != "pending":
            return req.get("status"), state["members"][req["member_id"]].get("saldo", 0)
        if now_ts() - int(req.get("created_ts", 0)) > 1800:
            req["status"] = "expired"
            return "expired", state["members"][req["member_id"]].get("saldo", 0)
        m = state["members"][req["member_id"]]
        qty = int(req["qty"])
        if int(m.get("saldo", 0)) < qty:
            req["status"] = "insufficient"
            return "insufficient", m.get("saldo", 0)
        m["saldo"] = int(m.get("saldo", 0)) - qty
        req["status"] = "approved"
        req["resolved_at"] = now_iso()
        state.setdefault("history", []).append({
            "at": now_iso(), "type": "approved", "member_id": req["member_id"],
            "qty": qty, "saldo": m["saldo"]
        })
        state["history"] = state["history"][-500:]
        return "approved", m["saldo"]
    status, saldo = update_state(mutate)
    return redirect(f"/a/{token}")


@app.post("/a/<token>/reject")
def reject(token):
    def mutate(state):
        req = state.get("requests", {}).get(token)
        if not req:
            return
        if req.get("status") == "pending":
            req["status"] = "rejected"
            req["resolved_at"] = now_iso()
            state.setdefault("history", []).append({
                "at": now_iso(), "type": "rejected", "member_id": req["member_id"],
                "qty": req["qty"]
            })
            state["history"] = state["history"][-500:]
    update_state(mutate)
    return redirect(f"/a/{token}")


@app.get("/qr/<mid>.png")
@admin_required
def qr_png(mid):
    state, _ = load_state()
    if mid not in state.get("members", {}):
        abort(404)
    url = f"{public_base()}/v/{mid}"
    qr = qrcode.QRCode(version=None, box_size=9, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(
        buf, mimetype="image/png",
        as_attachment=request.args.get("download") == "1",
        download_name=f"voucher-{mid}.png"
    )


@app.get("/admin/qrs")
@admin_required
def print_qrs():
    state, _ = load_state()
    cards = []
    for mid, m in state.get("members", {}).items():
        if not m.get("activo", True):
            continue
        p = decrypt_member(m)
        cards.append((p.get("nombre",""), f"""
          <div class="qr-card">
            <div class="brand">JCRC · TEMPORADA 2026/27</div>
            <img src="/qr/{mid}.png">
            <div class="name">{p.get('nombre','')}</div>
            <div>Socio {p.get('socio','')}</div>
            <div class="small">Voucher de invitaciones</div>
          </div>"""))
    cards.sort(key=lambda x: x[0].lower())
    body = '<div class="card no-print"><h1>QR para imprimir</h1><button class="btn btn-orange" onclick="window.print()">Imprimir</button></div><div class="qr-grid">' + "".join(x[1] for x in cards) + '</div>'
    extra = """<style>
    .qr-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.qr-card{background:#fff;border:2px solid #171717;border-top:8px solid #f47b20;border-radius:10px;text-align:center;padding:12px;break-inside:avoid}.qr-card img{width:170px;height:170px}.brand{font-weight:900}.name{font-size:18px;font-weight:800;margin-top:6px}.small{font-size:12px;color:#555}
    @media print{.top,.no-print{display:none!important}.wrap{max-width:none;margin:0;padding:0}.qr-grid{grid-template-columns:repeat(3,1fr);gap:8px}.qr-card{box-shadow:none}}
    </style>"""
    return page("QR para imprimir", body, extra)


@app.get("/admin/history")
@admin_required
def history():
    state, _ = load_state()
    rows = []
    for h in reversed(state.get("history", [])[-200:]):
        m = state.get("members", {}).get(h.get("member_id"), {})
        try:
            p = decrypt_member(m) if m else {}
        except Exception:
            p = {}
        rows.append(f"<tr><td>{h.get('at','')[:19].replace('T',' ')}</td><td>{h.get('type','')}</td><td>{p.get('nombre','')}</td><td>{h.get('qty','')}</td><td>{h.get('saldo','')}</td></tr>")
    body = f'<div class="card"><h1>Historial</h1><table><tr><th>Fecha</th><th>Evento</th><th>Titular</th><th>Cant.</th><th>Saldo</th></tr>{"".join(rows)}</table></div>'
    return page("Historial", body)


@app.get("/health")
def health():
    try:
        state, _ = load_state()
        return jsonify({"status": "ok", "members": len(state.get("members", {})), "github_persistence": bool(GITHUB_TOKEN)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.get("/privacy")
def privacy():
    return """<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Política de Privacidad - Jockey Club Río Cuarto</title></head>
    <body style="font-family:Arial;max-width:800px;margin:40px auto;line-height:1.5"><h1>Política de Privacidad</h1><h2>Jockey Club Río Cuarto</h2>
    <p>Esta aplicación gestiona vouchers de temporada, invitaciones y autorizaciones de ingreso.</p>
    <p>Los datos se utilizan únicamente para administrar el servicio, validar accesos y gestionar autorizaciones.</p>
    <p>No se venden ni alquilan datos personales a terceros.</p>
    <p>Los usuarios pueden solicitar acceso, modificación o eliminación de sus datos mediante los canales oficiales del Jockey Club Río Cuarto.</p></body></html>"""


# Se conservan los endpoints del webhook ya configurado en Meta, aunque esta versión no depende de la API.
@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge or "", 200
    return "Verification failed", 403


@app.post("/webhook")
def receive_webhook():
    payload = request.get_json(silent=True) or {}
    print("WHATSAPP WEBHOOK:", payload, flush=True)
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
