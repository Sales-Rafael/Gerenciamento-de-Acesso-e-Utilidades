# Projeto Gau — Gestão de Acesso e Utilidades

Sistema de controle de acesso e reserva de laboratórios. Os instrutores pedem a reserva
em um site com calendário, o administrador aprova, e no horário aprovado o **crachá RFID**
do instrutor destranca a porta do laboratório reservado, usando um **ESP32**.

**Tecnologias:** Python (Flask) · SQLite · ESP32 (Arduino) · leitor RFID RC522

## Como funciona

1. O **administrador** cadastra os instrutores e vincula o crachá de cada um.
2. O **instrutor** entra no site, escolhe laboratório, data e horário e envia a solicitação.
3. O administrador **aprova ou nega** a solicitação.
4. No horário aprovado, o instrutor encosta o crachá no leitor da porta. O ESP32 consulta
   o servidor, e se houver reserva aprovada para aquele laboratório, a fechadura é acionada.
5. Cada tentativa (liberada ou negada) fica registrada no histórico de acessos.

Funciona com **vários laboratórios**, sem limite: cada laboratório tem a sua porta e o seu ESP32.
A reserva de um laboratório não abre a porta de outro.

## Requisitos

- Python 3 instalado no computador que será o servidor
- Um ESP32 por porta, com leitor RC522, relé (ou fechadura), LEDs e buzzer
- ESP32 e servidor na **mesma rede Wi-Fi** (o ESP32 só enxerga redes de 2,4 GHz)
- Arduino IDE (ou similar) com suporte ao ESP32 e as bibliotecas **MFRC522** e **ArduinoJson**

## 1. Instalar o servidor

O servidor roda em um notebook/computador na rede dos ESP32.

**Windows (cmd):**

```bat
cd "Projeto Gau"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Linux / macOS:**

```bash
cd "Projeto Gau"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Criar o primeiro usuário (administrador)

O sistema **não vem com nenhum usuário**, e o site não tem tela de cadastro aberta.
O primeiro administrador é criado pelo terminal, com o script `criar_admin.py`.
Com o ambiente virtual ativo, dentro da pasta do projeto, rode:

```bash
python criar_admin.py
```

Ele pergunta três coisas:

| Pergunta | O que digitar |
|---|---|
| `Nome:` | Nome do administrador |
| `E-mail:` | E-mail que será usado para entrar no site |
| `Senha (mín. 6 caracteres):` | Senha (não aparece enquanto você digita) |

Se tudo der certo, aparece **"Administrador criado com sucesso."**
Se aparecer "Já existe um usuário com esse e-mail.", esse e-mail já foi cadastrado.

Na primeira execução, o banco de dados (`acesso.db`) e as chaves (`secret.key` e `api.key`)
também são criados automaticamente, e os laboratórios iniciais são cadastrados.

Para criar **outro administrador** depois, rode o `criar_admin.py` de novo com outro e-mail.

## 3. Iniciar o servidor

```bash
python app.py
```

No Windows, também dá para dar dois cliques no `iniciar.bat` (ele precisa do `venv` já criado).

O site abre em `http://localhost:5000` no próprio computador, e em
`http://IP_DO_COMPUTADOR:5000` nos outros aparelhos da rede (celular, tablet, outro PC).

Entre com o **e-mail e a senha** do administrador criado no passo anterior.

## 4. Cadastrar os instrutores e os crachás

Depois de entrar como administrador:

1. Abra **Usuários e crachás** e use **Criar usuário**. Informe nome, e-mail, senha
   (mínimo 6 caracteres) e o nível: **instrutor** ou **adm**.
2. Passe o e-mail e a senha para a pessoa. Ela entra no site e pode trocar a senha em **Meu perfil**.
3. Para vincular o crachá, descubra o **UID** dele: a pessoa encosta o crachá no leitor da porta.
   O UID aparece em **"Crachás lidos que ainda não têm dono"**. Copie e cole no cadastro do usuário
   (formato `04 A1 B2 C3`).
4. Na mesma tela dá para editar, desativar, reativar e apagar usuários.

## 5. Laboratórios

Na primeira execução são criados: Informática 101, Informática 104, Laboratório 4.0,
Laboratório Maker, Eletrônica, Elétrica Predial, Elétrica Industrial e Pneumática.

Em **Laboratórios** o administrador adiciona, edita, desativa, ativa e apaga laboratórios.
Cada um tem um **ID** (letras e/ou números). Esse ID é o `LAB_ID` do código do ESP32 daquela porta;
se mudar o ID no site, mude também no ESP32.

## 6. Reservas

- **Instrutor:** em **Calendário** escolhe o laboratório, a data (pode ser mais de um dia seguido),
  o horário e a turma, e envia a solicitação. Acompanha tudo em **Minhas reservas** e pode cancelar.
- **Administrador:** em **Solicitações** aprova ou nega (com explicação opcional).
  Os históricos ficam em **Histórico de decisões** e **Histórico de acessos**, e os dois podem ser
  baixados em **PDF**.

## 7. Configurar e gravar o ESP32

O código está em `Cod_Arduino/Cod_Arduino.ino`. Antes de gravar, preencha as configurações
no começo do arquivo:

```cpp
const char* WIFI_SSID  = "nome do wifi";
const char* WIFI_PASS  = "senha do wifi";
const char* SERVER_URL = "http://IP_DO_SERVIDOR:5000/api/acesso";
const char* API_KEY    = "conteúdo do arquivo api.key";
const char* LAB_ID     = "1";   // ID do laboratório (coluna "ID" na tela Laboratórios)
```

- `SERVER_URL`: use o **IP fixo** do computador servidor (no Windows, veja com `ipconfig`, linha *IPv4*).
- `API_KEY`: copie o conteúdo do arquivo `api.key` que o servidor gerou.
- `LAB_ID`: em cada ESP32 mude só esta linha.

### Ligações (pinos)

| Componente | Pino do ESP32 |
|---|---|
| RC522 SS (SDA) | 5 |
| RC522 RST | 22 |
| RC522 SCK / MISO / MOSI | 18 / 19 / 23 |
| Relé (fechadura) | 4 |
| LED verde (liberado) | 26 |
| LED vermelho (negado) | 27 |
| Buzzer passivo | 25 |
| LED azul (status da rede) | 32 |
| LED amarelo (status da rede) | 33 |

O relé é **ativo em LOW** (repouso em HIGH). Ao liberar o acesso, o pino do relé pulsa a
**10 Hz durante 1 segundo**, junto com um bip de aprovação.

### Significado dos LEDs

| LED | Significado |
|---|---|
| Verde | Acesso liberado |
| Vermelho | Acesso negado ou erro de comunicação (porta trancada) |
| Azul fixo | Wi-Fi e internet funcionando |
| Azul e amarelo alternando | Wi-Fi conectado, mas sem internet |
| Amarelo piscando rápido | Wi-Fi desconectado |

## 8. Deixar o computador funcionando como servidor

1. **IP fixo.** O ESP32 acessa o servidor pelo IP, então ele não pode mudar. Peça ao TI
   (ou configure no roteador) para reservar um IP para o computador.
2. **Firewall do Windows.** Na primeira vez, permita o acesso em **Rede privada**. Se o ESP32
   não conseguir conectar, libere a porta 5000 (cmd como administrador):

   ```bat
   netsh advfirewall firewall add rule name="Projeto Gau" dir=in action=allow protocol=TCP localport=5000
   ```
3. **Mesma rede.** Algumas redes (escolas, empresas) isolam os aparelhos entre si.
   Se for o caso, peça ao TI para liberar a comunicação.
4. **Computador ligado e acordado.** Se ele dormir ou desligar, as portas deixam de abrir.
5. **Data e hora corretas** (fuso de Brasília): as reservas dependem do relógio do servidor.

## Problemas comuns

- **Não consigo entrar no site:** o login é pelo **e-mail**, não pelo nome. Se esqueceu a senha de
  um instrutor, o administrador define uma nova em **Usuários e crachás**.
- **O ESP32 não conecta no servidor:** confira o `SERVER_URL` (IP certo, com `:5000`), se o
  servidor está rodando, o firewall e se os dois estão na mesma rede.
- **O ESP32 conecta, mas dá erro 401 / 403:** a `API_KEY` do ESP32 está diferente do `api.key` do servidor.
- **O crachá é lido, mas a porta não abre:** veja em **Histórico de acessos** o motivo da recusa
  (sem reserva aprovada, crachá sem dono, laboratório diferente ou fora do horário) e confira o `LAB_ID`.
- **Para gerar PDF** o servidor precisa da biblioteca `reportlab` (já está no `requirements.txt`).

## Segurança e backup

- O servidor usa **HTTP simples** e foi pensado para a **rede interna**. Não o exponha na internet.
- `acesso.db`, `secret.key` e `api.key` contêm dados e segredos reais: **nunca envie para o GitHub**.
  Também não envie o `.ino` com a senha do Wi-Fi e a chave da API preenchidas.
- **Backup:** todos os dados estão em `acesso.db`. Copie esse arquivo de vez em quando
  (por exemplo, para um pendrive).

## Estrutura do projeto

```
app.py              servidor, rotas e regras
schema.sql          tabelas do banco
criar_admin.py      cria administradores pelo terminal
requirements.txt    dependências do Python
iniciar.bat         atalho para iniciar o servidor no Windows
templates/          páginas HTML
static/             estilo e ícones
Cod_Arduino/        código do ESP32 (Cod_Arduino.ino)
```
