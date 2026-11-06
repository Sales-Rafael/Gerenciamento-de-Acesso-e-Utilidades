"""
Sistema de acesso ao laboratório (SENAI)
- Site com calendário e login (instrutor / administrador)
- API /api/acesso consultada pelo ESP32
"""
import hashlib
import io
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from xml.sax.saxutils import escape

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "acesso.db"))


def carrega_segredo(nome_env, arquivo):
    """Lê do ambiente ou gera e guarda em arquivo (para não mudar a cada reinício)."""
    valor = os.environ.get(nome_env)
    if valor:
        return valor
    caminho = os.path.join(BASE_DIR, arquivo)
    if not os.path.exists(caminho):
        with open(caminho, "w") as f:
            f.write(secrets.token_hex(24))
    with open(caminho) as f:
        return f.read().strip()


app = Flask(__name__)
app.config["SECRET_KEY"] = carrega_segredo("SECRET_KEY", "secret.key")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# JSON em uma linha só e com acentos de verdade (o debug do Flask deixaria "bonito", com \u00e1 etc.)
app.json.compact = True
app.json.ensure_ascii = False
# Chave que o ESP32 envia no cabeçalho X-API-Key
API_KEY = carrega_segredo("API_KEY", "api.key")

# Laboratórios criados automaticamente na primeira execução (depois o administrador
# edita, desativa, apaga ou cadastra novos na tela "Laboratórios").
LABS_INICIAIS = [
    "Informática 101", "Informática 104", "Laboratório 4.0", "Laboratório Maker",
    "Laboratório Eletrônica", "Laboratório Elétrica Predial",
    "Laboratório Elétrica Industrial", "Laboratório Pneumática",
]


# ------------------------------------------------------------------ banco
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def fecha_db(_erro):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
            db.executescript(f.read())
        # banco criado por versão anterior: acrescenta a coluna "oculto"
        colunas = [c["name"] for c in db.execute("PRAGMA table_info(logs_acesso)")]
        if "oculto" not in colunas:
            db.execute("ALTER TABLE logs_acesso ADD COLUMN oculto INTEGER NOT NULL DEFAULT 0")
            db.commit()
        # idem: acrescenta o motivo da recusa nas reservas
        colunas = [c["name"] for c in db.execute("PRAGMA table_info(reservas)")]
        if "motivo_recusa" not in colunas:
            db.execute("ALTER TABLE reservas ADD COLUMN motivo_recusa TEXT")
        if "turma" not in colunas:
            db.execute("ALTER TABLE reservas ADD COLUMN turma TEXT")
        if "grupo_id" not in colunas:
            db.execute("ALTER TABLE reservas ADD COLUMN grupo_id TEXT")
        if "decidido_por" not in colunas:
            db.execute("ALTER TABLE reservas ADD COLUMN decidido_por TEXT")
            db.execute("ALTER TABLE reservas ADD COLUMN decidido_em TEXT")
        if "visto" not in colunas:
            db.execute("ALTER TABLE reservas ADD COLUMN visto INTEGER NOT NULL DEFAULT 1")
        if "decidido_por_id" not in colunas:
            # id de quem decidiu: o nome mostrado acompanha o perfil atual da pessoa
            db.execute("ALTER TABLE reservas ADD COLUMN decidido_por_id INTEGER")
        if "oculto_usuario" not in colunas:
            # 1 = o dono apagou da lista "Minhas reservas"; a linha continua no histórico do administrador
            db.execute("ALTER TABLE reservas ADD COLUMN oculto_usuario INTEGER NOT NULL DEFAULT 0")
        if "cancelado_por" not in colunas:
            # quem cancelou agora é guardado à parte, para não apagar quem aprovou
            db.execute("ALTER TABLE reservas ADD COLUMN cancelado_por TEXT")
            db.execute("ALTER TABLE reservas ADD COLUMN cancelado_por_id INTEGER")
            db.execute("ALTER TABLE reservas ADD COLUMN cancelado_em TEXT")
            # cancelamentos antigos feitos pelo administrador gravavam o nome dele em decidido_por
            # (e o motivo "Cancelada pelo administrador."): passa essa informação para o lugar certo
            db.execute("""UPDATE reservas SET cancelado_por=decidido_por, cancelado_por_id=decidido_por_id,
                          cancelado_em=decidido_em, decidido_por=NULL, decidido_por_id=NULL, decidido_em=NULL
                          WHERE status='cancelada' AND motivo_recusa='Cancelada pelo administrador.'
                            AND decidido_por IS NOT NULL""")
        # decisões antigas (sem o id de quem decidiu): só liga quando o nome guardado bate com
        # exatamente um usuário. Não há palpite: as que não baterem ficam com o nome guardado.
        for r in db.execute("""SELECT DISTINCT decidido_por FROM reservas
                               WHERE decidido_por IS NOT NULL AND decidido_por_id IS NULL""").fetchall():
            donos = db.execute("SELECT id FROM usuarios WHERE nome=?", (r["decidido_por"],)).fetchall()
            if len(donos) == 1:
                db.execute("UPDATE reservas SET decidido_por_id=? WHERE decidido_por=? AND decidido_por_id IS NULL",
                           (donos[0]["id"], r["decidido_por"]))
        # ---- vários laboratórios ----
        # cadastra os laboratórios iniciais se a tabela ainda está vazia
        ja_teve = db.execute("SELECT 1 FROM sqlite_sequence WHERE name='laboratorios'").fetchone()
        if db.execute("SELECT COUNT(*) FROM laboratorios").fetchone()[0] == 0 and not ja_teve:
            db.executemany("INSERT INTO laboratorios (nome) VALUES (?)", [(n,) for n in LABS_INICIAIS])
        # banco de versão anterior (1 laboratório só): reservas e acessos antigos ficam no primeiro laboratório
        primeiro = db.execute("SELECT MIN(id) FROM laboratorios").fetchone()[0]
        for tabela in ("reservas", "logs_acesso"):
            colunas = [c["name"] for c in db.execute(f"PRAGMA table_info({tabela})")]
            if "laboratorio_id" not in colunas:
                db.execute(f"ALTER TABLE {tabela} ADD COLUMN laboratorio_id INTEGER REFERENCES laboratorios(id)")
                db.execute(f"UPDATE {tabela} SET laboratorio_id=?", (primeiro,))
        db.execute("CREATE INDEX IF NOT EXISTS idx_reservas_lab ON reservas (laboratorio_id, data, status)")
        # "ID" do laboratório (o que vai no ESP32): pode ter letras e números; o número interno (id) não muda nunca
        if "codigo" not in [c["name"] for c in db.execute("PRAGMA table_info(laboratorios)")]:
            db.execute("ALTER TABLE laboratorios ADD COLUMN codigo TEXT")
        db.execute("UPDATE laboratorios SET codigo = CAST(id AS TEXT) WHERE codigo IS NULL")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lab_codigo ON laboratorios (codigo COLLATE NOCASE)")
        db.commit()


def cria_usuario(nome, email, senha, nivel, uid=None):
    db = get_db()
    db.execute(
        "INSERT INTO usuarios (nome, email, senha_hash, nivel, uid_cracha) VALUES (?,?,?,?,?)",
        (nome.strip(), email.strip().lower(), generate_password_hash(senha), nivel,
         normaliza_uid(uid) or None),
    )
    db.commit()


def normaliza_uid(uid):
    """'04:a1:b2:c3' / '04 A1  B2 C3' -> '04 A1 B2 C3'"""
    if not uid:
        return ""
    limpo = re.sub(r"[^0-9A-Fa-f]", " ", uid).upper().split()
    return " ".join(limpo)


def lab_ativo(db, valor):
    """Laboratório ativo com esse id (vindo de formulário/API), ou None."""
    try:
        lid = int(valor)
    except (TypeError, ValueError):
        return None
    return db.execute("SELECT * FROM laboratorios WHERE id=? AND ativo=1", (lid,)).fetchone()


ID_LAB_OK = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


def codigo_livre(db, lid):
    """ID automático para laboratório novo: o próprio número, ou o próximo livre se alguém já usa esse."""
    n = lid
    while db.execute("SELECT 1 FROM laboratorios WHERE codigo = ? COLLATE NOCASE", (str(n),)).fetchone():
        n += 1
    return str(n)


# nome do laboratório de cada reserva
LAB = "lb.nome AS lab_nome"
JOIN_LAB = "LEFT JOIN laboratorios lb ON lb.id = r.laboratorio_id"


# ------------------------------------------------------- login e segurança
@app.before_request
def carrega_usuario_e_csrf():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = get_db().execute(
            "SELECT * FROM usuarios WHERE id=? AND ativo=1", (uid,)).fetchone()
        if g.user is None:
            session.clear()

    # proteção CSRF para formulários (a API do ESP32 usa chave própria)
    if request.method == "POST" and request.endpoint != "api_acesso":
        token = session.get("csrf")
        if not token or not secrets.compare_digest(token, request.form.get("csrf", "")):
            abort(400, "Sessão expirada. Volte e tente novamente.")


@app.context_processor
def contexto():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    pendentes = 0
    if g.get("user") and g.user["nivel"] == "adm":
        pendentes = get_db().execute(
            "SELECT COUNT(*) FROM reservas WHERE status='pendente'").fetchone()[0]
    novidades = 0
    if g.get("user"):
        novidades = get_db().execute(
            "SELECT COUNT(*) FROM reservas WHERE usuario_id=? AND visto=0 AND oculto_usuario=0",
            (g.user["id"],)).fetchone()[0]
    laboratorios = []
    if g.get("user"):
        laboratorios = get_db().execute("SELECT * FROM laboratorios WHERE ativo=1 ORDER BY id").fetchall()
    return dict(csrf=session["csrf"], user=g.get("user"),
                pendentes=pendentes, novidades=novidades, laboratorios=laboratorios)


@app.template_filter("nivel_nome")
def nivel_nome(nivel):
    return "Administrador" if nivel == "adm" else "Instrutor"


@app.template_filter("dh_br")
def dh_br(v):
    """'2026-10-29 19:50:12' -> '29/10 19:50'"""
    return f"{v[8:10]}/{v[5:7]} {v[11:16]}" if v and len(v) >= 16 else ""


@app.template_filter("data_br")
def data_br(d):
    return f"{d[8:10]}/{d[5:7]}/{d[0:4]}" if d else ""


def reserva_passou(r):
    """True se o horário final da reserva já passou (não dá mais para cancelar)."""
    return f'{r["data"]} {r["hora_fim"]}' <= datetime.now().strftime("%Y-%m-%d %H:%M")


app.add_template_filter(reserva_passou, "passou")

def sincroniza_nome_decisor(db, uid, nome_antigo, nome_novo):
    """Chamada quando alguém muda de nome. O nome guardado em texto (decidido_por) acompanha o
    perfil, então, se a conta for apagada um dia, o histórico fica com o último nome da pessoa."""
    db.execute("UPDATE reservas SET decidido_por=? WHERE decidido_por_id=?", (nome_novo, uid))
    db.execute("UPDATE reservas SET cancelado_por=? WHERE cancelado_por_id=?", (nome_novo, uid))
    if nome_antigo != nome_novo:
        # decisões antigas sem id que tinham o nome antigo: só vale se ninguém mais usa esse nome
        outro = db.execute("SELECT 1 FROM usuarios WHERE nome=? AND id != ?", (nome_antigo, uid)).fetchone()
        if outro is None:
            db.execute("""UPDATE reservas SET decidido_por_id=?, decidido_por=?
                          WHERE decidido_por_id IS NULL AND decidido_por=?""", (uid, nome_novo, nome_antigo))


# nome atual de quem decidiu (se a pessoa mudou o nome no perfil, aparece o novo;
# se a conta foi apagada, fica o nome que estava guardado na hora da decisão)
DECISOR = "COALESCE(d.nome, r.decidido_por) AS decisor"
JOIN_DECISOR = "LEFT JOIN usuarios d ON d.id = r.decidido_por_id"
# idem para quem cancelou
CANCELADOR = "COALESCE(c.nome, r.cancelado_por) AS cancelador"
JOIN_CANCELADOR = "LEFT JOIN usuarios c ON c.id = r.cancelado_por_id"


def linhas_historico(r):
    """O que aconteceu com a reserva, em ordem: [(rótulo, nome, quando), ...].
    Ex.: [("Aprovada", "Maria", "30/10 09:00"), ("Cancelada", "João", "30/10 10:00")]"""
    linhas = []
    if r["decisor"]:
        linhas.append(("Negada" if r["status"] == "negada" else "Aprovada", r["decisor"], r["decidido_em"]))
    if r["cancelador"]:
        linhas.append(("Cancelada", r["cancelador"], r["cancelado_em"]))
    return linhas


app.add_template_filter(linhas_historico, "historico")


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper


def adm_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if g.user["nivel"] != "adm":
            abort(403)
        return f(*a, **kw)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        u = get_db().execute(
            "SELECT * FROM usuarios WHERE email=? AND ativo=1", (email,)).fetchone()
        if u and check_password_hash(u["senha_hash"], senha):
            session.clear()
            session["user_id"] = u["id"]
            destino = request.args.get("next", "")
            return redirect(destino if destino.startswith("/") and not destino.startswith("//")
                            else url_for("calendario"))
        flash("E-mail ou senha incorretos.", "erro")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- calendário
@app.route("/")
@login_required
def calendario():
    agora = datetime.now()
    # data já preenchida no formulário: hoje (segunda a sexta) ou a próxima segunda (sábado e domingo)
    padrao = agora.date()
    if padrao.weekday() >= 5:
        padrao += timedelta(days=7 - padrao.weekday())
    return render_template("calendario.html", hoje=agora.strftime("%Y-%m-%d"),
                           data_padrao=padrao.strftime("%Y-%m-%d"))


@app.get("/api/eventos")
@login_required
def api_eventos():
    """Eventos do calendário (JSON)."""
    db = get_db()
    linhas = db.execute(
        f"""SELECT r.*, u.nome, {LAB} FROM reservas r JOIN usuarios u ON u.id = r.usuario_id
           {JOIN_LAB} WHERE r.status IN ('aprovada', 'pendente')""").fetchall()
    eventos = []
    for r in linhas:
        # pendentes só aparecem para o dono e para o administrador
        if r["status"] == "pendente" and g.user["nivel"] != "adm" and r["usuario_id"] != g.user["id"]:
            continue
        pendente = r["status"] == "pendente"
        eventos.append({
            "id": r["id"],
            "title": r["nome"] + (" (pendente)" if pendente else ""),
            "start": f'{r["data"]}T{r["hora_inicio"]}',
            "end": f'{r["data"]}T{r["hora_fim"]}',
            "color": "#e08a00" if pendente else "#2e7d32",
            "lab": r["laboratorio_id"],
            "lab_nome": r["lab_nome"] or "",
        })
    return jsonify(eventos)


def tem_conflito(db, lab_id, data, inicio, fim, ignora_id=None):
    """Existe reserva APROVADA, NO MESMO LABORATÓRIO, que se sobrepõe ao intervalo?"""
    sql = """SELECT 1 FROM reservas WHERE status='aprovada' AND laboratorio_id=? AND data=?
             AND ? < hora_fim AND ? > hora_inicio"""
    params = [lab_id, data, inicio, fim]
    if ignora_id:
        sql += " AND id != ?"
        params.append(ignora_id)
    return db.execute(sql, params).fetchone() is not None


@app.post("/reservas")
@login_required
def nova_reserva():
    lab = lab_ativo(get_db(), request.form.get("laboratorio"))
    data = request.form.get("data", "")
    inicio = request.form.get("hora_inicio", "")
    fim = request.form.get("hora_fim", "")
    turma = request.form.get("turma", "").strip()[:80]
    motivo = request.form.get("motivo", "").strip()[:200]
    repete = request.form.get("repete", "nao")
    try:
        qtd = int(request.form.get("qtd", "1") or 1)
    except ValueError:
        qtd = 1

    if lab is None:
        flash("Escolha o laboratório.", "erro")
        return redirect(url_for("calendario"))
    if not (data and inicio and fim and turma and motivo):
        flash("Preencha todos os campos: data, horários, turma e motivo.", "erro")
        return redirect(url_for("calendario"))
    if repete not in ("nao", "dias", "semanas") or (repete != "nao" and not request.form.get("qtd", "").strip()):
        flash("Informe quantos dias ou semanas devem ser reservados.", "erro")
        return redirect(url_for("calendario"))

    try:
        primeira = datetime.strptime(data, "%Y-%m-%d")
        datetime.strptime(inicio, "%H:%M")
        datetime.strptime(fim, "%H:%M")
    except ValueError:
        flash("Data ou horário inválido.", "erro")
        return redirect(url_for("calendario"))

    if int(inicio[3:5]) % 5 or int(fim[3:5]) % 5:
        flash("Os minutos do horário precisam ser de 5 em 5 (00, 05, 10 ... 55).", "erro")
        return redirect(url_for("calendario"))
    if fim <= inicio:
        flash("O horário final precisa ser depois do inicial.", "erro")
        return redirect(url_for("calendario"))
    if f"{data} {fim}" <= datetime.now().strftime("%Y-%m-%d %H:%M"):
        flash("Esse horário já passou.", "erro")
        return redirect(url_for("calendario"))

    # lista de datas: só um dia, dias seguidos ou toda semana
    if repete == "dias":
        qtd, passo = max(1, min(qtd, 31)), 1
    elif repete == "semanas":
        qtd, passo = max(1, min(qtd, 26)), 7
    else:
        qtd, passo = 1, 1
    datas = [(primeira + timedelta(days=passo * i)).strftime("%Y-%m-%d") for i in range(qtd)]

    db = get_db()
    livres = [d for d in datas if not tem_conflito(db, lab["id"], d, inicio, fim)]
    conflitos = [d for d in datas if d not in livres]
    if not livres:
        flash("Já existe uma reserva aprovada nesse horário neste laboratório." if qtd == 1
              else "Todas as datas escolhidas já têm reserva aprovada nesse horário neste laboratório.", "erro")
        return redirect(url_for("calendario"))

    grupo = secrets.token_hex(6) if len(livres) > 1 else None
    for d in livres:
        db.execute(
            "INSERT INTO reservas (usuario_id, laboratorio_id, data, hora_inicio, hora_fim, turma, motivo, grupo_id) VALUES (?,?,?,?,?,?,?,?)",
            (g.user["id"], lab["id"], d, inicio, fim, turma, motivo, grupo))
    db.commit()
    if len(livres) == 1:
        flash(f"Solicitação enviada ({lab['nome']})! Aguarde a aprovação do administrador.", "ok")
    else:
        flash(f"Solicitação enviada para {len(livres)} datas ({lab['nome']})! Aguarde a aprovação do administrador.", "ok")
    if conflitos:
        flash("Ficaram de fora (já reservadas): " + ", ".join(data_br(d)[:5] for d in conflitos) + ".", "erro")
    return redirect(url_for("calendario"))


@app.route("/minhas-reservas")
@login_required
def minhas_reservas():
    reservas = get_db().execute(
        f"SELECT r.*, {DECISOR}, {CANCELADOR}, {LAB} FROM reservas r {JOIN_DECISOR} {JOIN_CANCELADOR} {JOIN_LAB} "
        "WHERE r.usuario_id=? AND r.oculto_usuario=0 ORDER BY r.data DESC, r.hora_inicio DESC",
        (g.user["id"],)).fetchall()
    # as linhas já carregadas continuam com a marca "novo"; daqui para frente contam como vistas
    db = get_db()
    db.execute("UPDATE reservas SET visto=1 WHERE usuario_id=? AND visto=0", (g.user["id"],))
    db.commit()
    return render_template("minhas_reservas.html", reservas=reservas)


@app.post("/reservas/<int:rid>/cancelar")
@login_required
def cancelar_reserva(rid):
    db = get_db()
    r = db.execute("SELECT * FROM reservas WHERE id=?", (rid,)).fetchone()
    if r is None or (r["usuario_id"] != g.user["id"] and g.user["nivel"] != "adm"):
        abort(404)
    if r["status"] in ("pendente", "aprovada") and reserva_passou(r):
        flash("Essa reserva já passou e não pode mais ser cancelada.", "erro")
    elif r["status"] in ("pendente", "aprovada"):
        # quem cancelou fica registrado à parte; quem aprovou (decidido_*) não é alterado
        if r["usuario_id"] != g.user["id"]:      # cancelada pelo administrador: avisa o dono
            db.execute("""UPDATE reservas SET status='cancelada', visto=0,
                          motivo_recusa=COALESCE(motivo_recusa, 'Cancelada pelo administrador.'),
                          cancelado_por=?, cancelado_por_id=?, cancelado_em=datetime('now','localtime') WHERE id=?""",
                       (g.user["nome"], g.user["id"], rid))
        else:
            db.execute("""UPDATE reservas SET status='cancelada',
                          cancelado_por=?, cancelado_por_id=?, cancelado_em=datetime('now','localtime') WHERE id=?""",
                       (g.user["nome"], g.user["id"], rid))
        db.commit()
        flash("Reserva cancelada.", "ok")
    return redirect(request.form.get("voltar") or url_for("minhas_reservas"))


@app.post("/reservas/<int:rid>/apagar")
@login_required
def apagar_reserva(rid):
    """Tira de "Minhas reservas" uma solicitação cancelada, negada ou já finalizada (horário passou).
    Se ela tem decisão registrada, só some para o dono: continua no histórico de decisões do
    administrador. Se nunca teve decisão (ex.: pedido que expirou), é apagada de vez.
    O contrário também vale: quando o administrador apaga do histórico de decisões, some das duas listas."""
    db = get_db()
    r = db.execute("SELECT * FROM reservas WHERE id=?", (rid,)).fetchone()
    if r is None or (r["usuario_id"] != g.user["id"] and g.user["nivel"] != "adm"):
        abort(404)
    finalizada = r["status"] in ("pendente", "aprovada") and reserva_passou(r)
    if r["status"] not in ("negada", "cancelada") and not finalizada:
        flash("Só é possível apagar solicitações finalizadas, canceladas ou negadas.", "erro")
    else:
        if r["decidido_por"] is not None or r["cancelado_por"] is not None:
            db.execute("UPDATE reservas SET oculto_usuario=1 WHERE id=?", (rid,))
        else:
            db.execute("DELETE FROM reservas WHERE id=?", (rid,))
        db.commit()
        flash("Solicitação apagada do histórico.", "ok")
    return redirect(url_for("minhas_reservas"))


# ---------------------------------------------------------------- perfil
@app.route("/perfil")
@login_required
def perfil():
    return render_template("perfil.html")


@app.post("/perfil/dados")
@login_required
def perfil_dados():
    nome = request.form.get("nome", "").strip()[:120]
    email = request.form.get("email", "").strip().lower()
    if not nome or not re.match(r"^[^@\s]+@[^@\s]+$", email):
        flash("Preencha o nome e um e-mail válido.", "erro")
        return redirect(url_for("perfil"))
    try:
        db = get_db()
        db.execute("UPDATE usuarios SET nome=?, email=? WHERE id=?", (nome, email, g.user["id"]))
        sincroniza_nome_decisor(db, g.user["id"], g.user["nome"], nome)
        db.commit()
        flash("Seus dados foram atualizados.", "ok")
    except sqlite3.IntegrityError:
        flash("Esse e-mail já está em uso por outra pessoa.", "erro")
    return redirect(url_for("perfil"))


@app.post("/perfil/senha")
@login_required
def perfil_senha():
    atual = request.form.get("atual", "")
    nova = request.form.get("nova", "")
    confirma = request.form.get("confirma", "")
    if not check_password_hash(g.user["senha_hash"], atual):
        flash("A senha atual está incorreta.", "erro")
    elif len(nova) < 6:
        flash("A nova senha precisa ter pelo menos 6 caracteres.", "erro")
    elif nova != confirma:
        flash("A confirmação não é igual à nova senha.", "erro")
    else:
        db = get_db()
        db.execute("UPDATE usuarios SET senha_hash=? WHERE id=?", (generate_password_hash(nova), g.user["id"]))
        db.commit()
        flash("Senha alterada com sucesso.", "ok")
    return redirect(url_for("perfil"))


# ------------------------------------------------------------- administrador
def filtros_logs():
    """Filtros do histórico, vindos da URL: ?pessoa=...&de=AAAA-MM-DD&ate=AAAA-MM-DD&lab=ID"""
    def data_ok(v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            return ""
    lab = request.args.get("lab", "").strip()
    return {"pessoa": request.args.get("pessoa", "").strip()[:80],
            "de": data_ok(request.args.get("de", "")),
            "ate": data_ok(request.args.get("ate", "")),
            "lab": lab if lab.isdigit() else ""}


def consulta_logs(filtros):
    """Todos os registros que batem com o filtro (sem paginação)."""
    sql = """SELECT l.*, u.nome, lb.nome AS lab_nome FROM logs_acesso l
             LEFT JOIN usuarios u ON u.id=l.usuario_id
             LEFT JOIN laboratorios lb ON lb.id=l.laboratorio_id WHERE 1=1"""
    params = []
    if filtros["lab"]:
        sql += " AND l.laboratorio_id = ?"
        params.append(int(filtros["lab"]))
    if filtros["pessoa"]:
        # procura no nome da pessoa ou no UID do crachá
        sql += " AND (u.nome LIKE ? OR l.uid LIKE ?)"
        params += [f"%{filtros['pessoa']}%", f"%{filtros['pessoa']}%"]
    if filtros["de"]:
        sql += " AND substr(l.data_hora, 1, 10) >= ?"
        params.append(filtros["de"])
    if filtros["ate"]:
        sql += " AND substr(l.data_hora, 1, 10) <= ?"
        params.append(filtros["ate"])
    return get_db().execute(sql + " ORDER BY l.id DESC", params).fetchall()


def consulta_desconhecidos():
    return get_db().execute(
        """SELECT l.uid, MAX(l.data_hora) AS ultima,
                  (SELECT lb.nome FROM logs_acesso l2 JOIN laboratorios lb ON lb.id = l2.laboratorio_id
                    WHERE l2.uid = l.uid ORDER BY l2.id DESC LIMIT 1) AS lab_nome
           FROM logs_acesso l
           WHERE l.usuario_id IS NULL AND l.oculto = 0
             AND l.uid NOT IN (SELECT uid_cracha FROM usuarios WHERE uid_cracha IS NOT NULL)
           GROUP BY l.uid ORDER BY ultima DESC LIMIT 8""").fetchall()


def renderiza_parcial(modelo, **ctx):
    """Devolve (html, versão). A versão muda sempre que o conteúdo muda."""
    html = render_template(modelo, **ctx)
    return html, hashlib.md5(html.encode("utf-8")).hexdigest()


@app.route("/admin/solicitacoes")
@adm_required
def admin_solicitacoes():
    db = get_db()
    pendentes = db.execute(
        f"""SELECT r.*, u.nome, {LAB} FROM reservas r JOIN usuarios u ON u.id=r.usuario_id
           {JOIN_LAB} WHERE r.status='pendente' ORDER BY r.data, r.hora_inicio""").fetchall()
    proximas = db.execute(
        f"""SELECT r.*, u.nome, {DECISOR}, {CANCELADOR}, {LAB} FROM reservas r JOIN usuarios u ON u.id=r.usuario_id
           {JOIN_DECISOR} {JOIN_CANCELADOR} {JOIN_LAB}
           WHERE r.status='aprovada'
             AND r.data || ' ' || r.hora_fim > strftime('%Y-%m-%d %H:%M','now','localtime')
           ORDER BY r.data, r.hora_inicio""").fetchall()
    # pedidos de vários dias aparecem numa linha só (aprovar/negar vale para o grupo todo)
    pedidos, vistos = [], {}
    for r in pendentes:
        if r["grupo_id"] and r["grupo_id"] in vistos:
            vistos[r["grupo_id"]]["datas"].append(r["data"])
            continue
        p = {"id": r["id"], "nome": r["nome"], "datas": [r["data"]], "hora_inicio": r["hora_inicio"],
             "hora_fim": r["hora_fim"], "turma": r["turma"], "motivo": r["motivo"],
             "lab_nome": r["lab_nome"]}
        if r["grupo_id"]:
            vistos[r["grupo_id"]] = p
        pedidos.append(p)
    return render_template("admin_solicitacoes.html", pendentes=pedidos, proximas=proximas)


@app.route("/admin/decisoes")
@adm_required
def admin_decisoes():
    """Histórico de decisões: todas desde o início, com filtro (?pessoa=...&de=...&ate=...)."""
    f = filtros_logs()
    parcial, versao = renderiza_parcial("_decisoes_tabela.html", decisoes=consulta_decisoes(f), filtros=f)
    nomes = [r["nome"] for r in get_db().execute("SELECT nome FROM usuarios ORDER BY nome")]
    todos_labs = get_db().execute("SELECT * FROM laboratorios ORDER BY id").fetchall()
    return render_template("admin_decisoes.html", parcial_dec=parcial, versao_dec=versao, filtros=f, nomes=nomes,
                           todos_labs=todos_labs)


def consulta_decisoes(filtros):
    """Toda reserva que já teve decisão (aprovada, negada ou cancelada), da mais recente para a mais antiga.
    Filtro 'pessoa': nome do instrutor, de quem decidiu ou de quem cancelou.
    Filtros de data: valem para o dia da decisão ou do cancelamento."""
    sql = f"""SELECT r.*, u.nome, {DECISOR}, {CANCELADOR}, {LAB} FROM reservas r JOIN usuarios u ON u.id=r.usuario_id
              {JOIN_DECISOR} {JOIN_CANCELADOR} {JOIN_LAB}
              WHERE (r.decidido_por IS NOT NULL OR r.cancelado_por IS NOT NULL)"""
    params = []
    if filtros["lab"]:
        sql += " AND r.laboratorio_id = ?"
        params.append(int(filtros["lab"]))
    if filtros["pessoa"]:
        sql += """ AND (u.nome LIKE ? OR COALESCE(d.nome, r.decidido_por) LIKE ?
                        OR COALESCE(c.nome, r.cancelado_por) LIKE ?)"""
        params += [f"%{filtros['pessoa']}%"] * 3
    if filtros["de"] or filtros["ate"]:
        partes = []
        for coluna in ("r.decidido_em", "r.cancelado_em"):
            cond, p = [], []
            if filtros["de"]:
                cond.append(f"substr({coluna}, 1, 10) >= ?")
                p.append(filtros["de"])
            if filtros["ate"]:
                cond.append(f"substr({coluna}, 1, 10) <= ?")
                p.append(filtros["ate"])
            partes.append("(" + " AND ".join(cond) + ")")
            params += p
        sql += " AND (" + " OR ".join(partes) + ")"
    return get_db().execute(
        sql + " ORDER BY COALESCE(r.cancelado_em, r.decidido_em) DESC, r.id DESC", params).fetchall()


@app.post("/admin/reservas/<int:rid>/<acao>")
@adm_required
def decidir_reserva(rid, acao):
    if acao not in ("aprovar", "negar"):
        abort(404)
    db = get_db()
    r = db.execute("SELECT * FROM reservas WHERE id=?", (rid,)).fetchone()
    if r is None or r["status"] != "pendente":
        flash("Essa solicitação não está mais pendente.", "erro")
        return redirect(url_for("admin_solicitacoes"))
    # pedido de vários dias: a decisão vale para todas as datas ainda pendentes do grupo
    if r["grupo_id"]:
        alvo = db.execute("SELECT * FROM reservas WHERE grupo_id=? AND status='pendente' ORDER BY data",
                          (r["grupo_id"],)).fetchall()
    else:
        alvo = [r]

    if acao == "aprovar":
        aprovadas, negadas = [], []
        for x in alvo:
            if tem_conflito(db, x["laboratorio_id"], x["data"], x["hora_inicio"], x["hora_fim"], ignora_id=x["id"]):
                negadas.append(x)
            else:
                aprovadas.append(x)
        if len(alvo) == 1 and negadas:
            flash("Não dá para aprovar: conflita com uma reserva já aprovada.", "erro")
            return redirect(url_for("admin_solicitacoes"))
        for x in aprovadas:
            db.execute("""UPDATE reservas SET status='aprovada', visto=0, decidido_por=?, decidido_por_id=?,
                          decidido_em=datetime('now','localtime') WHERE id=?""", (g.user["nome"], g.user["id"], x["id"]))
        for x in negadas:
            db.execute("""UPDATE reservas SET status='negada', visto=0, decidido_por=?, decidido_por_id=?,
                          decidido_em=datetime('now','localtime'),
                          motivo_recusa='Já existia outra reserva aprovada neste laboratório, data e horário.' WHERE id=?""",
                       (g.user["nome"], g.user["id"], x["id"]))
        db.commit()
        if len(alvo) == 1:
            flash("Reserva aprovada.", "ok")
        else:
            flash(f"{len(aprovadas)} datas aprovadas.", "ok")
            if negadas:
                flash("Negadas por conflito: " + ", ".join(data_br(x["data"])[:5] for x in negadas) + ".", "erro")
        return redirect(url_for("admin_solicitacoes"))

    # o motivo é opcional; quem solicitou vê o texto em "Minhas reservas"
    motivo_recusa = request.form.get("motivo_recusa", "").strip()[:300] or None
    for x in alvo:
        db.execute("""UPDATE reservas SET status='negada', motivo_recusa=?, visto=0, decidido_por=?, decidido_por_id=?,
                      decidido_em=datetime('now','localtime') WHERE id=?""",
                   (motivo_recusa, g.user["nome"], g.user["id"], x["id"]))
    db.commit()
    flash("Reserva negada." if len(alvo) == 1 else f"{len(alvo)} datas negadas.", "ok")
    return redirect(url_for("admin_solicitacoes"))


@app.route("/admin/laboratorios", methods=["GET", "POST"])
@adm_required
def admin_laboratorios():
    """Cadastro dos laboratórios (cada um é uma porta com o seu ESP32)."""
    db = get_db()
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()[:80]
        codigo = request.form.get("codigo", "").strip()
        if not nome:
            flash("Digite o nome do laboratório.", "erro")
        elif codigo and not ID_LAB_OK.match(codigo):
            flash("O ID pode ter letras, números, hífen e sublinhado (sem espaços), até 20 caracteres.", "erro")
        elif codigo and db.execute("SELECT 1 FROM laboratorios WHERE codigo = ? COLLATE NOCASE", (codigo,)).fetchone():
            flash(f"O ID {codigo} já é de outro laboratório.", "erro")
        else:
            try:
                cur = db.execute("INSERT INTO laboratorios (nome, codigo) VALUES (?, ?)", (nome, codigo or None))
                if not codigo:
                    db.execute("UPDATE laboratorios SET codigo=? WHERE id=?", (codigo_livre(db, cur.lastrowid), cur.lastrowid))
                db.commit()
                flash("Laboratório cadastrado.", "ok")
            except sqlite3.IntegrityError:
                db.rollback()
                flash("Já existe um laboratório com esse nome.", "erro")
        return redirect(url_for("admin_laboratorios"))
    lista = db.execute("SELECT * FROM laboratorios ORDER BY id").fetchall()
    return render_template("admin_laboratorios.html", lista=lista)


@app.post("/admin/laboratorios/<int:lid>/editar")
@adm_required
def editar_laboratorio(lid):
    """Salva o ID (letras e/ou números) e o nome."""
    db = get_db()
    lab = db.execute("SELECT * FROM laboratorios WHERE id=?", (lid,)).fetchone()
    if lab is None:
        abort(404)
    if not lab["ativo"]:
        flash("Ative o laboratório antes de editar.", "erro")
        return redirect(url_for("admin_laboratorios"))
    nome = request.form.get("nome", "").strip()[:80]
    codigo = request.form.get("codigo", "").strip()
    if not nome:
        flash("Digite o nome do laboratório.", "erro")
    elif not ID_LAB_OK.match(codigo):
        flash("O ID pode ter letras, números, hífen e sublinhado (sem espaços), até 20 caracteres.", "erro")
    elif db.execute("SELECT 1 FROM laboratorios WHERE codigo = ? COLLATE NOCASE AND id != ?", (codigo, lid)).fetchone():
        flash(f"O ID {codigo} já é de outro laboratório.", "erro")
    else:
        try:
            db.execute("UPDATE laboratorios SET codigo=?, nome=? WHERE id=?", (codigo, nome, lid))
            db.commit()
            flash("Laboratório salvo.", "ok")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("Já existe um laboratório com esse nome.", "erro")
    return redirect(url_for("admin_laboratorios"))


@app.post("/admin/laboratorios/<int:lid>/ativo")
@adm_required
def alterna_laboratorio(lid):
    """Desativa / reativa. Desativado não recebe novas solicitações e a porta não abre; o histórico fica."""
    db = get_db()
    lab = db.execute("SELECT * FROM laboratorios WHERE id=?", (lid,)).fetchone()
    if lab is None:
        abort(404)
    db.execute("UPDATE laboratorios SET ativo = 1 - ativo WHERE id=?", (lid,))
    db.commit()
    flash(f"{lab['nome']} {'desativado' if lab['ativo'] else 'ativado'}.", "ok")
    return redirect(url_for("admin_laboratorios"))


@app.post("/admin/laboratorios/<int:lid>/apagar")
@adm_required
def apagar_laboratorio(lid):
    """Apaga definitivamente um laboratório, mas só se ele estiver desativado."""
    db = get_db()
    lab = db.execute("SELECT * FROM laboratorios WHERE id=?", (lid,)).fetchone()
    if lab is None:
        abort(404)
    if lab["ativo"]:
        flash("Desative o laboratório antes de apagá-lo.", "erro")
    else:
        # as reservas dele saem junto (chave estrangeira); os registros de acesso ficam no
        # histórico, só que sem o vínculo com o laboratório apagado
        db.execute("DELETE FROM reservas WHERE laboratorio_id=?", (lid,))
        db.execute("UPDATE logs_acesso SET laboratorio_id=NULL WHERE laboratorio_id=?", (lid,))
        db.execute("DELETE FROM laboratorios WHERE id=?", (lid,))
        db.commit()
        flash(f"{lab['nome']} apagado.", "ok")
    return redirect(url_for("admin_laboratorios"))


@app.route("/admin/usuarios", methods=["GET", "POST"])
@adm_required
def admin_usuarios():
    db = get_db()
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        nivel = request.form.get("nivel", "instrutor")
        uid = normaliza_uid(request.form.get("uid"))
        if not nome or not email or len(senha) < 6 or nivel not in ("instrutor", "adm"):
            flash("Preencha nome, e-mail e uma senha de pelo menos 6 caracteres.", "erro")
        else:
            try:
                cria_usuario(nome, email, senha, nivel, uid)
                flash("Usuário criado.", "ok")
            except sqlite3.IntegrityError:
                flash("E-mail ou crachá já cadastrado.", "erro")
        return redirect(url_for("admin_usuarios"))

    usuarios = db.execute("SELECT * FROM usuarios ORDER BY nome").fetchall()
    parcial, versao = renderiza_parcial("_desconhecidos.html", desconhecidos=consulta_desconhecidos())
    return render_template("admin_usuarios.html", usuarios=usuarios,
                           parcial_desc=parcial, versao_desc=versao)


@app.get("/admin/usuarios/desconhecidos")
@adm_required
def parcial_desconhecidos():
    """Trecho 'crachás sem dono', consultado a cada poucos segundos pela página."""
    html, versao = renderiza_parcial("_desconhecidos.html", desconhecidos=consulta_desconhecidos())
    return jsonify(v=versao, html=html)


@app.post("/admin/usuarios/<int:uid_user>/editar")
@adm_required
def editar_usuario(uid_user):
    """Salva nome, e-mail e crachá. Usuário desativado não pode ser editado."""
    db = get_db()
    u = db.execute("SELECT * FROM usuarios WHERE id=?", (uid_user,)).fetchone()
    if u is None:
        abort(404)
    if not u["ativo"]:
        flash("Reative o usuário antes de editar.", "erro")
        return redirect(url_for("admin_usuarios"))

    nome = request.form.get("nome", "").strip()[:120]
    email = request.form.get("email", "").strip().lower()
    uid = normaliza_uid(request.form.get("uid"))
    nivel = request.form.get("nivel", u["nivel"])
    nova_senha = request.form.get("senha", "")
    if not nome or not re.match(r"^[^@\s]+@[^@\s]+$", email):
        flash("Preencha o nome e um e-mail válido.", "erro")
        return redirect(url_for("admin_usuarios"))
    if nivel not in ("instrutor", "adm"):
        nivel = u["nivel"]
    if uid_user == g.user["id"]:
        nivel = u["nivel"]            # ninguém muda o próprio nível (evita ficar sem administrador)
    if nova_senha and len(nova_senha) < 6:
        flash("A nova senha precisa ter pelo menos 6 caracteres.", "erro")
        return redirect(url_for("admin_usuarios"))
    try:
        db.execute("UPDATE usuarios SET nome=?, email=?, uid_cracha=?, nivel=? WHERE id=?",
                   (nome, email, uid or None, nivel, uid_user))
        if nova_senha:
            db.execute("UPDATE usuarios SET senha_hash=? WHERE id=?",
                       (generate_password_hash(nova_senha), uid_user))
        sincroniza_nome_decisor(db, uid_user, u["nome"], nome)
        db.commit()
        extra = []
        if nivel != u["nivel"]:
            extra.append(f"agora é {nivel_nome(nivel)}")
        if nova_senha:
            extra.append("senha redefinida")
        flash("Usuário salvo" + (" (" + ", ".join(extra) + ")." if extra else "."), "ok")
    except sqlite3.IntegrityError:
        flash("Esse e-mail ou crachá já está cadastrado para outra pessoa.", "erro")
    return redirect(url_for("admin_usuarios"))


@app.post("/admin/usuarios/<int:uid_user>/ativo")
@adm_required
def alterna_ativo(uid_user):
    if uid_user == g.user["id"]:
        flash("Você não pode desativar a si mesmo.", "erro")
    else:
        db = get_db()
        db.execute("UPDATE usuarios SET ativo = 1 - ativo WHERE id=?", (uid_user,))
        db.commit()
    return redirect(url_for("admin_usuarios"))


@app.post("/admin/usuarios/<int:uid_user>/apagar")
@adm_required
def apagar_usuario(uid_user):
    """Apaga definitivamente um usuário, mas só se ele estiver desativado."""
    db = get_db()
    u = db.execute("SELECT * FROM usuarios WHERE id=?", (uid_user,)).fetchone()
    if u is None:
        abort(404)
    if uid_user == g.user["id"]:
        flash("Você não pode apagar a si mesmo.", "erro")
    elif u["ativo"]:
        flash("Desative o usuário antes de apagá-lo.", "erro")
    else:
        # as reservas dele precisam sair junto (chave estrangeira); os registros de
        # acesso ficam no histórico, só que sem o vínculo com o usuário apagado
        db.execute("DELETE FROM reservas WHERE usuario_id=?", (uid_user,))
        # decisões que ele tomou em reservas de outras pessoas: mantém o último nome, sem vínculo
        db.execute("UPDATE reservas SET decidido_por=?, decidido_por_id=NULL WHERE decidido_por_id=?",
                   (u["nome"], uid_user))
        db.execute("UPDATE reservas SET cancelado_por=?, cancelado_por_id=NULL WHERE cancelado_por_id=?",
                   (u["nome"], uid_user))
        db.execute("UPDATE logs_acesso SET usuario_id=NULL, oculto=1 WHERE usuario_id=?", (uid_user,))
        db.execute("DELETE FROM usuarios WHERE id=?", (uid_user,))
        db.commit()
        flash(f"Usuário {u['nome']} apagado.", "ok")
    return redirect(url_for("admin_usuarios"))


@app.post("/admin/crachas-desconhecidos/apagar")
@adm_required
def apagar_cracha_desconhecido():
    """Tira um crachá da lista 'sem dono' (o histórico de acessos é mantido)."""
    uid = normaliza_uid(request.form.get("uid"))
    if uid:
        db = get_db()
        db.execute("UPDATE logs_acesso SET oculto=1 WHERE usuario_id IS NULL AND uid=?", (uid,))
        db.commit()
        flash("Registro removido da lista.", "ok")
    return redirect(url_for("admin_usuarios"))


@app.post("/admin/logs/apagar")
@adm_required
def apagar_logs():
    """Apaga TODO o histórico de acessos."""
    db = get_db()
    db.execute("DELETE FROM logs_acesso")
    db.commit()
    flash("Histórico de acessos apagado.", "ok")
    return redirect(url_for("admin_logs"))


@app.route("/admin/logs")
@adm_required
def admin_logs():
    f = filtros_logs()
    parcial, versao = renderiza_parcial("_logs_tabela.html", logs=consulta_logs(f), filtros=f)
    nomes = [r["nome"] for r in get_db().execute("SELECT nome FROM usuarios ORDER BY nome")]
    todos_labs = get_db().execute("SELECT * FROM laboratorios ORDER BY id").fetchall()
    return render_template("admin_logs.html", parcial=parcial, versao=versao, filtros=f, nomes=nomes,
                           todos_labs=todos_labs)


def pdf_indisponivel():
    """Mensagem amigável quando a biblioteca reportlab não está instalada."""
    try:
        import reportlab  # noqa: F401
        return False
    except ImportError:
        flash("Para gerar PDF instale a biblioteca: pip install reportlab (e reinicie o servidor).", "erro")
        return True


def monta_pdf_tabela(titulo_doc, subtitulo, cabecalho, linhas, larguras):
    """Gera um PDF (A4 paisagem) com uma tabela e devolve os bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    azul = colors.HexColor("#0b4a8f")
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("t", parent=estilos["Title"], textColor=azul, fontSize=18, alignment=0)
    suave = ParagraphStyle("s", parent=estilos["Normal"], textColor=colors.HexColor("#5a6778"), fontSize=9)
    cel = ParagraphStyle("c", parent=estilos["Normal"], fontSize=8.5, leading=11)

    def p(texto):
        return Paragraph(escape(str(texto or "-")), cel)

    elementos = [Paragraph(escape(titulo_doc), titulo), Paragraph(subtitulo, suave), Spacer(1, 6 * mm)]
    dados = [cabecalho] + [[p(c) for c in l] for l in linhas]
    if not linhas:
        dados.append([p("Nenhum registro."), *[""] * (len(cabecalho) - 1)])

    tabela = Table(dados, repeatRows=1, colWidths=larguras)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), azul),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f7fc")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d6dee8")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if not linhas:
        estilo.append(("SPAN", (0, 1), (-1, 1)))
    tabela.setStyle(TableStyle(estilo))
    elementos.append(tabela)

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#5a6778"))
        canvas.drawRightString(doc.pagesize[0] - 12 * mm, 8 * mm, f"Página {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm, title=titulo_doc,
                            author="Acesso ao Laboratório")
    doc.build(elementos, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()


def descricao_filtro(f, campo="pessoa/UID", datas=""):
    partes = []
    if f["pessoa"]:
        partes.append(f"{campo} contém \"{f['pessoa']}\"")
    if f["de"]:
        partes.append(datas + "a partir de " + data_br(f["de"]))
    if f["ate"]:
        partes.append(datas + "até " + data_br(f["ate"]))
    if f.get("lab"):
        lab = get_db().execute("SELECT nome FROM laboratorios WHERE id=?", (int(f["lab"]),)).fetchone()
        if lab:
            partes.append(f"laboratório \"{lab['nome']}\"")
    return " · Filtro: " + ", ".join(partes) if partes else ""


def monta_pdf_logs(logs, filtros=None):
    liberados = sum(1 for l in logs if l["liberado"])
    gerado = datetime.now().strftime("%d/%m/%Y às %H:%M")
    sub = (f"Gerado em {gerado} por {escape(g.user['nome'])} · "
           f"{len(logs)} registros ({liberados} liberados, {len(logs) - liberados} negados)"
           + escape(descricao_filtro(filtros or {"pessoa": "", "de": "", "ate": ""})))
    linhas = [[l["data_hora"], l["nome"], l["uid"], l["lab_nome"], "Liberado" if l["liberado"] else "Negado", l["motivo"]]
              for l in logs]
    return monta_pdf_tabela("Histórico de acessos — Laboratórios", sub,
                            ["Data/hora", "Pessoa", "UID", "Laboratório", "Resultado", "Motivo"], linhas,
                            [34 * MM, 46 * MM, 36 * MM, 46 * MM, 22 * MM, None])


MM = 2.8346456692913385   # 1 mm em pontos


def monta_pdf_decisoes(decisoes, filtros=None):
    gerado = datetime.now().strftime("%d/%m/%Y às %H:%M")
    sub = (f"Gerado em {gerado} por {escape(g.user['nome'])} · {len(decisoes)} reservas"
           + escape(descricao_filtro(filtros or {"pessoa": "", "de": "", "ate": ""},
                                     campo="pessoa", datas="decisões ")))
    linhas = []
    for r in decisoes:
        d = r["data"]
        linhas.append([r["nome"], r["lab_nome"], f"{d[8:10]}/{d[5:7]}/{d[0:4]}", f"{r['hora_inicio']} – {r['hora_fim']}",
                       r["turma"], r["status"].capitalize(),
                       " · ".join(f"{rot} por {nome} ({dh_br(quando)})" if quando else f"{rot} por {nome}"
                                  for rot, nome, quando in linhas_historico(r)),
                       r["motivo_recusa"]])
    return monta_pdf_tabela("Decisões sobre reservas — Laboratórios", sub,
                            ["Instrutor", "Laboratório", "Data", "Horário", "Turma", "Situação", "Histórico", "Motivo informado"],
                            linhas, [30 * MM, 38 * MM, 20 * MM, 24 * MM, 22 * MM, 20 * MM, 62 * MM, None])


@app.post("/admin/decisoes/apagar")
@adm_required
def apagar_decisoes():
    """Apaga TODO o histórico de decisões (não só o que está filtrado).
    Ficam de fora as reservas aprovadas que ainda não aconteceram: apagá-las tiraria o acesso do instrutor."""
    db = get_db()
    r = db.execute("""DELETE FROM reservas
                      WHERE (decidido_por IS NOT NULL OR cancelado_por IS NOT NULL)
                        AND (status IN ('negada', 'cancelada')
                             OR (status = 'aprovada'
                                 AND data || ' ' || hora_fim <= strftime('%Y-%m-%d %H:%M', 'now', 'localtime')))""")
    db.commit()
    flash("Histórico de decisões apagado." if r.rowcount else "Não havia nada para apagar.", "ok")
    return redirect(url_for("admin_decisoes"))


@app.get("/admin/decisoes/pdf")
@adm_required
def baixar_decisoes_pdf():
    """Baixa em PDF o histórico de decisões (respeitando o filtro da tela)."""
    if pdf_indisponivel():
        return redirect(url_for("admin_decisoes"))
    f = filtros_logs()
    nome = "decisoes-reservas-" + datetime.now().strftime("%Y%m%d-%H%M") + ".pdf"
    return send_file(io.BytesIO(monta_pdf_decisoes(consulta_decisoes(f), f)), mimetype="application/pdf",
                     as_attachment=True, download_name=nome)


@app.get("/admin/decisoes/parcial")
@adm_required
def parcial_decisoes():
    """Tabela de decisões, consultada a cada poucos segundos pela página."""
    f = filtros_logs()
    html, versao = renderiza_parcial("_decisoes_tabela.html", decisoes=consulta_decisoes(f), filtros=f)
    return jsonify(v=versao, html=html)


@app.get("/admin/logs/pdf")
@adm_required
def baixar_logs_pdf():
    """Baixa em PDF o histórico de acessos (respeitando o filtro da tela)."""
    if pdf_indisponivel():
        return redirect(url_for("admin_logs"))
    f = filtros_logs()
    logs = consulta_logs(f)
    nome = "historico-acessos-" + datetime.now().strftime("%Y%m%d-%H%M") + ".pdf"
    return send_file(io.BytesIO(monta_pdf_logs(logs, f)), mimetype="application/pdf",
                     as_attachment=True, download_name=nome)


@app.get("/admin/logs/parcial")
@adm_required
def parcial_logs():
    """Tabela do histórico, consultada a cada poucos segundos pela página."""
    f = filtros_logs()
    html, versao = renderiza_parcial("_logs_tabela.html", logs=consulta_logs(f), filtros=f)
    return jsonify(v=versao, html=html)


# --------------------------------------------------------- API do ESP32
@app.post("/api/acesso")
def api_acesso():
    chave = request.headers.get("X-API-Key", "")
    if not secrets.compare_digest(chave, API_KEY):
        return jsonify(erro="nao autorizado"), 401

    dados = request.get_json(silent=True) or {}
    uid = normaliza_uid(dados.get("uid", ""))
    if not uid:
        return jsonify(erro="uid ausente"), 400

    db = get_db()
    # cada ESP32 informa de qual laboratório (porta) é, pelo ID: {"uid": "...", "lab": "MAKER"}
    codigo = str(dados.get("lab") if dados.get("lab") is not None else "").strip()
    if not codigo:
        return jsonify(erro="lab ausente"), 400
    lab = db.execute("SELECT * FROM laboratorios WHERE codigo = ? COLLATE NOCASE", (codigo,)).fetchone()
    if lab is None:
        return jsonify(erro="laboratorio inexistente"), 400

    agora = datetime.now()
    u = db.execute("SELECT * FROM usuarios WHERE uid_cracha=? AND ativo=1", (uid,)).fetchone()

    if u is None:
        liberado, motivo, nome = False, "crachá não cadastrado", ""
    elif not lab["ativo"]:
        liberado, motivo, nome = False, "laboratório desativado", u["nome"]
    else:
        nome = u["nome"]
        reserva = db.execute(
            """SELECT 1 FROM reservas WHERE usuario_id=? AND laboratorio_id=? AND status='aprovada'
               AND data=? AND hora_inicio <= ? AND ? < hora_fim""",
            (u["id"], lab["id"], agora.strftime("%Y-%m-%d"), agora.strftime("%H:%M"),
             agora.strftime("%H:%M"))).fetchone()
        if reserva:
            liberado, motivo = True, "reserva aprovada"
        else:
            liberado, motivo = False, "sem reserva aprovada neste laboratório e horário"

    db.execute("INSERT INTO logs_acesso (uid, usuario_id, laboratorio_id, liberado, motivo) VALUES (?,?,?,?,?)",
               (uid, u["id"] if u else None, lab["id"], int(liberado), motivo))
    db.commit()
    return jsonify(autorizado=liberado, nome=nome, motivo=motivo, lab=lab["nome"])


init_db()

if __name__ == "__main__":
    # Servidor no notebook (veja o README). debug=False: não deixe o modo debug ligado na rede.
    app.run(host="0.0.0.0", port=5000, debug=False)
