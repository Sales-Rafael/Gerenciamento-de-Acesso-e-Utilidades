CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    senha_hash  TEXT NOT NULL,
    nivel       TEXT NOT NULL CHECK (nivel IN ('instrutor', 'adm')),
    uid_cracha  TEXT UNIQUE,
    ativo       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS laboratorios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- número interno (não muda)
    codigo      TEXT,                                -- o "ID" que o administrador vê e que vai no ESP32 (LAB_ID); letras e/ou números
    nome        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    ativo       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reservas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id   INTEGER NOT NULL REFERENCES usuarios(id),
    laboratorio_id INTEGER REFERENCES laboratorios(id),   -- em qual laboratório é a reserva
    data         TEXT NOT NULL,      -- AAAA-MM-DD
    hora_inicio  TEXT NOT NULL,      -- HH:MM
    hora_fim     TEXT NOT NULL,      -- HH:MM
    turma        TEXT,               -- turma que vai usar o laboratório
    motivo       TEXT,
    status       TEXT NOT NULL DEFAULT 'pendente'
                 CHECK (status IN ('pendente', 'aprovada', 'negada', 'cancelada')),
    criado_em    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    motivo_recusa TEXT,              -- explicação opcional dada pelo administrador ao negar/cancelar
    grupo_id     TEXT,               -- pedidos de vários dias compartilham o mesmo grupo
    visto        INTEGER NOT NULL DEFAULT 1,  -- 0 = o administrador alterou e o instrutor ainda não viu
    decidido_por TEXT,               -- nome de quem aprovou / negou / cancelou (guardado como texto)
    decidido_em  TEXT,               -- quando
    decidido_por_id INTEGER,         -- id de quem decidiu (o nome exibido vem do perfil atual)
    cancelado_por TEXT,              -- nome de quem cancelou (guardado como texto)
    cancelado_por_id INTEGER,        -- id de quem cancelou
    cancelado_em TEXT,               -- quando foi cancelada
    oculto_usuario INTEGER NOT NULL DEFAULT 0  -- 1 = o dono apagou de "Minhas reservas" (segue no histórico do administrador)
);

CREATE TABLE IF NOT EXISTS logs_acesso (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT NOT NULL,
    usuario_id  INTEGER,
    laboratorio_id INTEGER REFERENCES laboratorios(id),   -- em qual porta (ESP32) foi a leitura
    data_hora   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    liberado    INTEGER NOT NULL,
    motivo      TEXT,
    oculto      INTEGER NOT NULL DEFAULT 0   -- 1 = escondido da lista de crachás sem dono
);

CREATE INDEX IF NOT EXISTS idx_reservas_data ON reservas (data, status);
