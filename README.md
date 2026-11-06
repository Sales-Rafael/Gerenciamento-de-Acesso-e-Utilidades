# Projeto Gau — Gestão de Acesso e Utilidades (Flask + SQLite + ESP32)

Site com calendário e login (instrutor / administrador) e a API que os ESP32 consultam
para destrancar as portas. Funciona com vários laboratórios (sem limite):
cada laboratório tem a sua porta, com o seu ESP32.

O servidor roda em um **notebook** na mesma rede Wi-Fi dos ESP32. Os instrutores acessam
o site pelo navegador (computador, tablet ou celular) usando o IP do notebook.

## 1. Rodar no notebook

Requisitos: Python 3 instalado.

**Windows (cmd):**

```bat
cd "Projeto Gau"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python criar_admin.py
python app.py
```

**Linux / macOS:**

```bash
cd "Projeto Gau"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python criar_admin.py
python app.py
```

O site abre em `http://localhost:5000` no próprio notebook e em `http://IP_DO_NOTEBOOK:5000`
nos outros aparelhos da rede.

Na primeira execução são criados: `acesso.db` (banco), `secret.key` e `api.key`.
A **chave da API** que o ESP32 usa está em `api.key`: copie o conteúdo para a
constante `API_KEY` do código do ESP32.

> **Importante:** `acesso.db`, `secret.key` e `api.key` contêm dados e segredos reais.
> Nunca envie esses arquivos para o GitHub.

## 2. Deixar o notebook funcionando como servidor

1. **IP fixo.** O ESP32 acessa o servidor pelo IP do notebook, então ele não pode mudar.
   Descubra o IP com `ipconfig` (linha *IPv4*) e peça ao TI (ou configure no roteador)
   para reservar esse IP para o notebook.
2. **Firewall do Windows.** Na primeira vez que o `app.py` rodar, o Windows pergunta se
   deseja permitir o acesso: marque **Rede privada** e permita. Se não apareceu a pergunta
   (ou o ESP32 não consegue conectar), libere a porta 5000 abrindo o cmd como administrador:

   ```bat
   netsh advfirewall firewall add rule name="Projeto Gau" dir=in action=allow protocol=TCP localport=5000
   ```
3. **Mesma rede.** O notebook e os ESP32 precisam estar na mesma rede. Algumas redes
   (escolas, empresas) isolam os aparelhos entre si; se for o caso, peça ao TI para liberar
   a comunicação entre eles.
4. **Notebook ligado e acordado.** Se ele dormir ou desligar, as portas deixam de abrir.
   Em *Configurações > Sistema > Energia*, desative a suspensão enquanto estiver na tomada.
5. **Horário certo.** As reservas dependem da data e hora do notebook. Confira se o
   relógio e o fuso (Brasília) estão corretos.
6. **Em uso real, desligue o modo debug.** Na última linha do `app.py`, troque
   `debug=True` por `debug=False`.

### Iniciar o servidor sempre que precisar

Depois da instalação, basta abrir o cmd na pasta do projeto e rodar:

```bat
venv\Scripts\activate
python app.py
```

Se quiser um atalho, crie um arquivo `iniciar.bat` na pasta do projeto com:

```bat
@echo off
cd /d "%~dp0"
call venv\Scripts\activate
python app.py
pause
```

Para iniciar junto com o Windows, coloque um atalho desse arquivo na pasta de inicialização
(`Win + R`, digite `shell:startup`).

## 3. Fluxo de uso

0. Na primeira execução os laboratórios já são criados (Informática 101, Informática 104,
   Laboratório 4.0, Maker, Eletrônica, Elétrica Predial, Elétrica Industrial e Pneumática).
   Em **Laboratórios** o administrador adiciona, edita (nome e ID), desativa, ativa e apaga.
   O **ID** (letras e/ou números) é o `LAB_ID` do código do ESP32 daquela porta (se mudar o ID, mude também no ESP32).
1. O administrador entra, vai em **Usuários e crachás** e cadastra os instrutores.
2. Para descobrir o UID de um crachá: a pessoa encosta o crachá na porta; o UID
   aparece em "Crachás lidos que ainda não têm dono". Copie e cole no usuário.
3. Instrutor entra, escolhe o laboratório, a data e o horário e envia a solicitação.
4. O administrador aprova em **Solicitações**.
5. No horário aprovado, o crachá do instrutor destranca a porta **do laboratório reservado**
   (a reserva de um laboratório não abre a porta de outro).
   Cada tentativa fica em **Histórico de acessos**.

## 4. ESP32

O código está em `Cod_Arduino/Cod_Arduino.ino`. Antes de gravar, preencha as configurações
no começo do arquivo:

```cpp
const char* WIFI_SSID  = "nome do wifi";
const char* WIFI_PASS  = "senha do wifi";
const char* SERVER_URL = "http://IP_DO_NOTEBOOK:5000/api/acesso";
const char* API_KEY    = "conteúdo do arquivo api.key";
const char* LAB_ID     = "1";   // ID do laboratório (coluna "ID" na tela Laboratórios)
```

- `SERVER_URL` usa o **IP fixo do notebook**. É HTTP simples, dentro da rede interna.
- Em cada ESP32 mude só a linha `LAB_ID`.
- O ESP32 só enxerga redes Wi-Fi de **2,4 GHz**.
- **Não envie para o GitHub** o arquivo com a senha do Wi-Fi e a chave da API preenchidas.

### API usada pelo ESP32

`POST /api/acesso` com o cabeçalho `X-API-Key: <conteúdo de api.key>` e o corpo
`{"uid": "04 11 22 33", "lab": "MAKER"}` (`lab` = ID do laboratório daquela porta; pode ser texto ou número).
Resposta: `{"autorizado": true, "nome": "Fulano", "motivo": "reserva aprovada", "lab": "Laboratório Maker"}`.

## 5. Backup

Todos os dados estão em `acesso.db` (ao atualizar um banco antigo, as reservas e acessos
que já existiam ficam no primeiro laboratório da lista). Copie esse arquivo de vez em quando
(por exemplo, para um pendrive ou outro computador).

## Estrutura

```
app.py              servidor, rotas e regras
schema.sql          tabelas do banco
criar_admin.py      cria o primeiro administrador
requirements.txt    dependências do Python
templates/          páginas HTML
static/estilo.css   visual
Cod_Arduino/        código do ESP32 (Cod_Arduino.ino)
```
