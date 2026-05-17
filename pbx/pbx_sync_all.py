import os
import sys
import io
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import time
import pandas as pd
import shutil
import logging
from datetime import datetime
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# common モジュールのパス解決
_ROOT = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from common.ssh_mysql_connector import MySQLSSHConnector

# .envファイルの読み込み（frozen時はMEIPASS、py時はohp-sync/ルート）
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

dotenv_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path)

# 作業用ディレクトリ設定（exe時はdist/配下、py時はohp-sync/配下）
if getattr(sys, 'frozen', False):
    EXEC_DIR = os.path.dirname(sys.executable)
else:
    EXEC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

WORK_DIR = os.path.normpath(os.path.join(EXEC_DIR, "work"))
ARCHIVE_DIR = os.path.normpath(os.path.join(WORK_DIR, "archive"))
EXPORT_DIR = WORK_DIR  # 出力もダウンロードもwork内で完結

# ✅ work/とarchive/を初回起動時に自動作成
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# ✅ .envから読み込んだ環境変数
PBX_LOGIN_USER = os.getenv("PBX_LOGIN_USER")
PBX_PASSWORD = os.getenv("PBX_PASSWORD")

# ログ設定（ohp-sync/logs/ に出力）
_LOG_DIR = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, 'pbx_sync_all.log')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(_log_file, encoding='utf-8', mode='a'),
        logging.StreamHandler(sys.stdout),
    ]
)

def _handle_exception(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    logging.error("未キャッチ例外", exc_info=(exc_type, exc_value, exc_tb))

sys.excepthook = _handle_exception

def log_and_print(message):
    print(message)
    logging.info(message)

def archive_existing_file(filepath, label):
    if os.path.exists(filepath):
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{label}.csv"
        shutil.move(filepath, os.path.join(ARCHIVE_DIR, filename))

def export_new_pbx_memberlist():
    db = MySQLSSHConnector()
    conn = db.connection
    cursor = conn.cursor()
    try:
        cursor.execute("CALL get_new_pbx_memberlist();")
        results = cursor.fetchall()
        logging.debug(f"[export] 結果セット#1: {len(results)}件")

        # PyMySQLマルチ結果セット対応: 全結果セットを走査し、最後の非空セットを使用
        set_index = 2
        while cursor.nextset():
            next_results = cursor.fetchall()
            logging.debug(f"[export] 結果セット#{set_index}: {len(next_results)}件")
            if next_results:
                results = next_results
            set_index += 1

        logging.debug(f"[export] 最終採用件数: {len(results)}件")

        if not results:
            log_and_print("🔸 新しいレコードがありません。処理を終了します。")
            return None

        df = pd.DataFrame(results)
        upload_path = os.path.join(WORK_DIR, "upload.csv")
        archive_existing_file(upload_path, "upload")

        df.to_csv(upload_path, index=False, encoding="utf-8-sig")
        log_and_print(f"✅ upload.csvをエクスポートしました: {upload_path}")
        return upload_path
    finally:
        cursor.close()
        db.close()

def upload_to_pbx_site(csv_path):
    if csv_path is None or not os.path.isfile(csv_path):
        log_and_print(f"❌ ファイルパスが無効: {csv_path}")
        return

    absolute_csv_path = os.path.abspath(csv_path)
    log_and_print(f"✅ アップロードファイルパス確認: {absolute_csv_path}")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    try:
        driver.get("http://e-phone.jp/login/")

        # ✅ 環境変数の値を確認
        log_and_print(f"👉 環境変数: PBX_LOGIN_USER = {PBX_LOGIN_USER}")
        log_and_print(f"👉 環境変数: PBX_PASSWORD = {PBX_PASSWORD}")

        # ✅ ユーザー名フィールドを取得できるか確認
        try:
            username_input = driver.find_element(By.XPATH, "/html/body/form/table/tbody/tr[1]/td/input")
            if username_input is None:
                log_and_print("❌ ユーザー名入力フィールドが取得できませんでした！")
                driver.quit()
                return
            else:
                log_and_print("✅ ユーザー名入力フィールド取得成功")
        except Exception as e:
            log_and_print(f"❌ ユーザー名フィールド取得エラー: {e}")
            driver.quit()
            return

        # ✅ ユーザー名入力
        log_and_print("👉 ユーザー名入力します")
        username_input.send_keys(PBX_LOGIN_USER)

        password_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "/html/body/form/table/tbody/tr[3]/td/input"))
        )
        log_and_print("👉 パスワード入力します")
        password_input.send_keys(PBX_PASSWORD)

        log_and_print("👉 ログインボタン押します")
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/form/table/tbody/tr[4]/td/input"))
        ).click()

        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/a[3]"))
        ).click()
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/a[4]"))
        ).click()

        file_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "/html/body/form/table/tbody/tr[1]/td/input"))
        )
        file_input.send_keys(absolute_csv_path)

        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/form/table/tbody/tr[3]/td/input"))
        ).click()
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/form/table/tbody/tr[4]/td/input"))
        ).click()
        time.sleep(2)

        log_and_print("✅ PBXサイトへのアップロード完了")

    finally:
        driver.quit()

def scrape_and_download():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    prefs = {
        "download.default_directory": WORK_DIR,
        "download.prompt_for_download": False,
        "directory_upgrade": True
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    try:
        driver.get("http://e-phone.jp/login/")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "/html/body/form/table/tbody/tr[1]/td/input"))
        ).send_keys(PBX_LOGIN_USER)
        driver.find_element(By.XPATH, "/html/body/form/table/tbody/tr[3]/td/input").send_keys(PBX_PASSWORD)
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/form/table/tbody/tr[4]/td/input"))
        ).click()

        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/a[3]"))
        ).click()
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/a[5]"))
        ).click()

        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/form/table/tbody/tr[1]/td/input[2]"))
        ).click()
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/form/table/tbody/tr[2]/td/input"))
        ).click()
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/form/table/tbody/tr[3]/td/input"))
        ).click()

        # ダウンロード完了を待機（最大60秒）
        import glob as _glob
        deadline = time.time() + 60
        while time.time() < deadline:
            crdownload = _glob.glob(os.path.join(WORK_DIR, "*.crdownload"))
            finished = _glob.glob(os.path.join(WORK_DIR, "addressbook*.csv"))
            if finished and not crdownload:
                break
            time.sleep(1)
        else:
            log_and_print("⚠️ ダウンロードがタイムアウトしました（60秒）")

        log_and_print("✅ ダウンロード操作完了")
    finally:
        driver.quit()

def rename_latest_download():
    import glob
    files = glob.glob(os.path.join(WORK_DIR, "addressbook*.csv"))
    if not files:
        log_and_print("❌ ダウンロードファイルが見つかりません")
        return None
    latest = max(files, key=os.path.getctime)
    download_path = os.path.join(WORK_DIR, "download.csv")
    archive_existing_file(download_path, "download")
    shutil.move(latest, download_path)
    log_and_print(f"✅ download.csvにリネーム: {download_path}")
    return download_path

def upsert_addressbook(csv_path):
    db_connector = MySQLSSHConnector()
    conn = db_connector.connection
    cursor = conn.cursor()

    try:
        df = pd.read_csv(csv_path, encoding="utf-8", dtype={"電話番号": str})
        df.columns = [col.lower() for col in df.columns]

        sql = """
        INSERT INTO pbx_addressbook (
            id, 名前, カナ, 電話番号, 短縮番号, グループ, 着信拒否, メモ
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            名前=VALUES(名前),
            カナ=VALUES(カナ),
            電話番号=VALUES(電話番号),
            短縮番号=VALUES(短縮番号),
            グループ=VALUES(グループ),
            着信拒否=VALUES(着信拒否),
            メモ=VALUES(メモ)
        """

        success_count = 0
        for index, row in df.iterrows():
            cursor.execute(sql, (
                row.get("id") if pd.notna(row.get("id")) else None,
                row.get("名前") if pd.notna(row.get("名前")) else None,
                row.get("カナ") if pd.notna(row.get("カナ")) else None,
                row.get("電話番号") if pd.notna(row.get("電話番号")) else None,
                row.get("短縮番号") if pd.notna(row.get("短縮番号")) else None,
                row.get("グループ") if pd.notna(row.get("グループ")) else None,
                row.get("着信拒否") if pd.notna(row.get("着信拒否")) else None,
                row.get("メモ") if pd.notna(row.get("メモ")) else None
            ))
            success_count += 1

        conn.commit()
        log_and_print(f"✅ pbx_addressbookにUPSERT完了: {success_count}件")

    finally:
        cursor.close()
        db_connector.close()

def main():
    logging.info("=" * 60)
    logging.info("=== PBX同期処理 開始 ===")
    start_time = datetime.now()

    try:
        upload_csv = export_new_pbx_memberlist()
        if not upload_csv:
            logging.info("新規レコードなし。処理を終了します。")
            return

        upload_to_pbx_site(upload_csv)
        scrape_and_download()

        download_csv = rename_latest_download()
        if download_csv:
            upsert_addressbook(download_csv)

        logging.info("=== PBX同期処理 完了 ===")
    except Exception as e:
        logging.error(f"致命的エラー: {e}", exc_info=True)
        raise
    finally:
        elapsed = datetime.now() - start_time
        logging.info(f"経過時間: {elapsed}")
        logging.info("=" * 60)

if __name__ == "__main__":
    main()
