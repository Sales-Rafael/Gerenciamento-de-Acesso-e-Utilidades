"""Cria o primeiro administrador. Uso: python criar_admin.py"""
import getpass
import sqlite3

from app import app, cria_usuario, init_db

init_db()
nome = input("Nome: ").strip()
email = input("E-mail: ").strip()
senha = getpass.getpass("Senha (mín. 6 caracteres): ")
if len(senha) < 6:
    raise SystemExit("Senha muito curta.")

with app.app_context():
    try:
        cria_usuario(nome, email, senha, "adm")
        print("Administrador criado com sucesso.")
    except sqlite3.IntegrityError:
        print("Já existe um usuário com esse e-mail.")
