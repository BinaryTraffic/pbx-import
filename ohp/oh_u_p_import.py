import json
import os
import logging
import time
import sys
import requests as req_lib
from datetime import datetime
from seleniumwire import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import Select
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv
import tempfile

# common モジュールのパス解決
_ROOT = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from common.ssh_mysql_connector import MySQLSSHConnector

_env_path = os.path.join(sys._MEIPASS if getattr(sys, 'frozen', False) else _ROOT, '.env')
load_dotenv(_env_path)

logging.basicConfig(
    filename="oh_u_p_import.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
    filemode="w",
)

def log_and_print(message):
    print(message)
    logging.info(message)

def format_datetime(iso_datetime):
    if not iso_datetime:
        return None
    try:
        parsed_date = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
        return parsed_date.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        log_and_print(f"日時変換エラー: {iso_datetime} - {e}")
        return None

def switch_account(driver, option_index):
    select_element = driver.find_element(By.XPATH, '/html/body/div[1]/div/div/header/div/div[1]/div/select')
    select = Select(select_element)
    select.select_by_index(option_index)
    log_and_print(f"アカウントが切り替えられました: オプション {option_index}")
    try:
        WebDriverWait(driver, 10).until(
            EC.invisibility_of_element_located((By.ID, "loadingOverlay"))
        )
    except Exception:
        time.sleep(2)

def login(driver, branch):
    log_and_print("ログイン処理を開始します。")

    branch_to_select_index = {
        "0": 0,
        "1": 1,
        "2": 2,
    }

    branch_str = str(branch)
    log_and_print(f"指定されたブランチ: {branch_str}")
    log_and_print(f"選択インデックス: {branch_to_select_index.get(branch_str)}")

    driver.get("https://admin.onehomeplus.jp/shop/login")
    email = os.getenv("OH_EMAIL")
    password = os.getenv("OH_PASSWORD")

    try:
        inputs = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, "c-input"))
        )
        inputs[0].send_keys(email)
        inputs[1].send_keys(password)
        driver.find_element(By.CLASS_NAME, "c-btn-add-a").click()
        WebDriverWait(driver, 15).until(EC.url_contains('/shop/'))
        switch_account(driver, branch_to_select_index[branch_str])
        update_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_and_print("ログイン処理が完了しました。")
        return update_dt
    except Exception as e:
        log_and_print(f"ログイン中にエラーが発生しました: {e}")
        return None

def extract_auth_token(driver, url_keyword, timeout=20):
    """傍受済みXHRからAuthorizationトークンを抽出"""
    start = time.time()
    while time.time() - start < timeout:
        for request in driver.requests:
            if url_keyword in request.url and request.headers.get('Authorization'):
                return request.headers.get('Authorization')
        time.sleep(0.5)
    return None

def fetch_pets_data(driver, shop_id):
    log_and_print("ペットデータ取得を開始します。")
    driver.get("https://admin.onehomeplus.jp/shop/customer")

    pets_api_base = f"https://api.onehomeplus.jp/api/v1/salon/shops/{shop_id}/pets"

    # 認証トークンを取得（初回XHRを待機）
    auth_token = extract_auth_token(driver, pets_api_base)

    if auth_token:
        log_and_print("認証トークン取得成功。ページネーションAPIで取得します。")
        return fetch_pets_data_api(shop_id, auth_token, pets_api_base)
    else:
        log_and_print("認証トークン取得失敗。スクロール方式にフォールバックします。")
        return fetch_pets_data_scroll(driver, shop_id, pets_api_base)

def fetch_pets_data_api(shop_id, auth_token, pets_api_base):
    """requests ライブラリでページネーション取得（スクロール不要）"""
    all_pets = {}
    all_users = {}
    all_branch = {}
    limit = 100
    offset = 0
    headers = {'Authorization': auth_token}

    while True:
        params = {'order_type': 'registered_date', 'order_ad': 'desc', 'limit': limit, 'offset': offset}
        try:
            response = req_lib.get(pets_api_base, headers=headers, params=params, timeout=15)
            data = response.json().get('data', [])
        except Exception as e:
            log_and_print(f"APIリクエストエラー: {e}")
            break

        if not data:
            log_and_print("ページネーション完了")
            break

        for pet in data:
            pet_id = pet.get("id")
            if pet_id and pet_id not in all_pets:
                all_pets[pet_id] = pet
            user_data = pet.get("user")
            if user_data:
                all_users[user_data["id"]] = user_data
                user_shop = user_data.get("user_shop")
                if user_shop:
                    all_branch[user_shop["id"]] = user_shop

        offset += limit
        log_and_print(f"累積pets件数: {len(all_pets)} 件, 累積users件数: {len(all_users)} 件")

    return all_users, list(all_pets.values()), all_branch

def fetch_pets_data_scroll(driver, shop_id, pets_api_base):
    """フォールバック：スクロール方式（認証トークン取得失敗時）"""
    all_pets = {}
    all_users = {}
    all_branch = {}
    new_pets_count = 0

    while True:
        pet_data_response = None
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight);")
        time.sleep(5)

        for request in driver.requests:
            if request.response and pets_api_base in request.url:
                try:
                    pet_data_json = json.loads(request.response.body.decode("utf-8"))
                    new_pets = pet_data_json.get("data", [])
                    for pet in new_pets:
                        pet_id = pet.get("id")
                        if pet_id and pet_id not in all_pets:
                            all_pets[pet_id] = pet
                        user_data = pet.get("user")
                        if user_data:
                            all_users[user_data["id"]] = user_data
                            user_shop = user_data.get("user_shop")
                            if user_shop:
                                all_branch[user_shop["id"]] = user_shop
                    if len(all_pets) > new_pets_count:
                        log_and_print(f"累積pets件数: {len(all_pets)} 件")
                        new_pets_count = len(all_pets)
                    if not new_pets:
                        return all_users, list(all_pets.values()), all_branch
                    pet_data_response = True
                except Exception as e:
                    log_and_print(f"ペットデータ取得エラー: {e}")
                    return all_users, list(all_pets.values()), all_branch
        if not pet_data_response:
            log_and_print("APIリクエストが見つかりません。終了します。")
            break
    return all_users, list(all_pets.values()), all_branch

def upsert_user_data(cursor, user_data, import_at):
    query = """
    INSERT INTO oh_users (
      id, first_name, last_name, first_name_kana, last_name_kana,
      phone_number, email, nickname, instagram, postal_code,
      province, municipalities, address_line, enabled,
      updated_at, created_at, import_at)
    VALUES (%s, %s, %s, %s, %s,%s, %s, %s, %s, %s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
        first_name=VALUES(first_name),
        last_name=VALUES(last_name),
        first_name_kana=VALUES(first_name_kana),
        last_name_kana=VALUES(last_name_kana),
        phone_number=VALUES(phone_number),
        email=VALUES(email),
        nickname=VALUES(nickname),
        instagram=VALUES(instagram),
        postal_code=VALUES(postal_code),
        province=VALUES(province),
        municipalities=VALUES(municipalities),
        address_line=VALUES(address_line),
        updated_at=VALUES(updated_at),
        enabled=VALUES(enabled),
        import_at=VALUES(import_at)
    """
    cursor.execute(query, (
        user_data.get("id"), user_data.get("first_name"), user_data.get("last_name"),
        user_data.get("first_name_kana"), user_data.get("last_name_kana"),
        user_data.get("phone_number"), user_data.get("email"), user_data.get("nickname"),
        user_data.get("instagram"), user_data.get("postal_code"), user_data.get("province"),
        user_data.get("municipalities"), user_data.get("address_line"),
        int(user_data.get("enabled", 1)), format_datetime(user_data.get("created_at")),
        format_datetime(user_data.get("updated_at")), import_at
    ))

def upsert_pet_data(cursor, pet_data, import_at):
    query = """
    INSERT INTO oh_pets (
      id, user_id, name, sex, birthday, enabled,
      created_at, updated_at, import_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,%s)
    ON DUPLICATE KEY UPDATE
        name=VALUES(name), sex=VALUES(sex), birthday=VALUES(birthday),
        updated_at=VALUES(updated_at), enabled=VALUES(enabled), import_at=VALUES(import_at)
    """
    for pet in pet_data:
        cursor.execute(query, (
            pet.get("id"), pet.get("user_id"), pet.get("name"), pet.get("sex"),
            pet.get("birthday"), int(pet.get("enabled", 1)),
            format_datetime(pet.get("created_at")), format_datetime(pet.get("updated_at")), import_at
        ))

def upsert_branch_data(cursor, branch_data, import_at):
    query = """
    INSERT INTO oh_user_shop (
        id, user_id, shop_id, user_no, phone_number2, phone_number3,
        phone_number_remarks, phone_number_remarks2, phone_number_remarks3,
        email2, email3, email_remarks, email_remarks2, email_remarks3,
        memo, postal_code, province, municipalities, address_line,
        reservation_count, days_since_last_reservation,
        created_at, updated_at, is_line_user
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        user_id=VALUES(user_id), shop_id=VALUES(shop_id), user_no=VALUES(user_no),
        phone_number2=VALUES(phone_number2), phone_number3=VALUES(phone_number3),
        phone_number_remarks=VALUES(phone_number_remarks), phone_number_remarks2=VALUES(phone_number_remarks2),
        phone_number_remarks3=VALUES(phone_number_remarks3), email2=VALUES(email2), email3=VALUES(email3),
        email_remarks=VALUES(email_remarks), email_remarks2=VALUES(email_remarks2), email_remarks3=VALUES(email_remarks3),
        memo=VALUES(memo), postal_code=VALUES(postal_code), province=VALUES(province),
        municipalities=VALUES(municipalities), address_line=VALUES(address_line),
        reservation_count=VALUES(reservation_count), days_since_last_reservation=VALUES(days_since_last_reservation),
        updated_at=VALUES(updated_at), is_line_user=VALUES(is_line_user)
    """
    cursor.execute(query, (
        branch_data.get("id"), branch_data.get("user_id"), branch_data.get("shop_id"),
        branch_data.get("user_no"), branch_data.get("phone_number2"), branch_data.get("phone_number3"),
        branch_data.get("phone_number_remarks"), branch_data.get("phone_number_remarks2"), branch_data.get("phone_number_remarks3"),
        branch_data.get("email2"), branch_data.get("email3"), branch_data.get("email_remarks"),
        branch_data.get("email_remarks2"), branch_data.get("email_remarks3"), branch_data.get("memo"),
        branch_data.get("postal_code"), branch_data.get("province"), branch_data.get("municipalities"),
        branch_data.get("address_line"), branch_data.get("reservation_count"),
        branch_data.get("days_since_last_reservation"), format_datetime(branch_data.get("created_at")),
        format_datetime(branch_data.get("updated_at")), int(branch_data.get("is_line_user", False))
    ))

def upsert_all_data(connection, users, pets, branches, import_at):
    cursor = connection.cursor()
    log_and_print(f"ユーザーデータ: {len(users)} 件")
    for user in users.values():
        try:
            upsert_user_data(cursor, user, import_at)
        except Exception as e:
            log_and_print(f"ユーザーUPSERTエラー: {e}")
    connection.commit()
    log_and_print(f"ペットデータ: {len(pets)} 件")
    for pet in pets:
        try:
            upsert_pet_data(cursor, [pet], import_at)
        except Exception as e:
            log_and_print(f"ペットUPSERTエラー: {e}")
    connection.commit()
    log_and_print(f"支店データ: {len(branches)} 件")
    for branch in branches.values():
        try:
            upsert_branch_data(cursor, branch, import_at)
        except Exception as e:
            log_and_print(f"支店UPSERTエラー: {e}")
    connection.commit()
    cursor.close()

def main():
    branch_shop_ids = {0: "107859", 1: "513530", 2: "513605"}
    if len(sys.argv) < 2 or not sys.argv[1].startswith("branch="):
        print("Usage: python oh_u_p_import.py branch=0")
        return

    try:
        branch = int(sys.argv[1].split("=")[1])
        shop_id = branch_shop_ids[branch]
    except Exception:
        print("無効な branch パラメータです。例: branch=0")
        return

    start_time = datetime.now()
    log_and_print(f"支店 {branch} の処理を開始します。")

    db_connector = MySQLSSHConnector()
    connection = db_connector.connection

    chrome_options = Options()
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-features=UseChromeOSDirectML")
    chrome_options.add_argument("--log-level=3")  # ERROR のみ
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])  # DevTools表示抑止


    # ✅ 一時ユーザーデータディレクトリを指定してセッション競合を回避
    temp_profile = tempfile.mkdtemp()
    chrome_options.add_argument(f"--user-data-dir={temp_profile}")

    driver = None
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        import_at = login(driver, branch)
        users, pets, branches = fetch_pets_data(driver, shop_id)
        upsert_all_data(connection, users, pets, branches, import_at)
    finally:
        db_connector.close()
        if driver:
            driver.quit()
        elapsed = (datetime.now() - start_time).total_seconds()
        log_and_print(f"支店 {branch} の処理が完了しました。経過時間: {elapsed} 秒")

if __name__ == "__main__":
    main()

