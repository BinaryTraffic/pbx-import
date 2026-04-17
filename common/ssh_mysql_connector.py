import os
import pymysql
from sshtunnel import SSHTunnelForwarder
from dotenv import load_dotenv

class MySQLSSHConnector:
    def __init__(self):
        self.server = SSHTunnelForwarder(
            (os.getenv("SSH_HOST"), int(os.getenv("SSH_PORT"))),
            ssh_username=os.getenv("SSH_USER"),
            ssh_pkey=os.getenv("SSH_KEY_PATH"),
            remote_bind_address=(os.getenv("DB_HOST"), int(os.getenv("DB_PORT")))
        )
        self.server.start()

        self.connection = pymysql.connect(
            host="127.0.0.1",  # SSHトンネル経由のローカル接続
            port=self.server.local_bind_port,
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor
        )

    def close(self):
        """接続が閉じられていない場合のみクローズ"""
        if self.connection and self.connection.open:
            self.connection.close()
        if self.server:
            self.server.stop()
