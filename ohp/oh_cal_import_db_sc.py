import re
import os
import time
import json
import logging
import sys
import io
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dateutil import parser
from tqdm import tqdm
from seleniumwire import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv

# common モジュールのパス解決
_ROOT = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from common.ssh_mysql_connector import MySQLSSHConnector

# .env の読み込み
_env_path = os.path.join(sys._MEIPASS if getattr(sys, 'frozen', False) else _ROOT, '.env')
load_dotenv(_env_path)

# デフォルト値
# グローバルスコープで定義
loop_count = 0
branch_index = None
BRANCH_MAP = {
    0: 107859,  # 八幡山店
    1: 513530,  # 芝店
    2: 513605   # 目黒店
}

# ログ設定（ohp-sync/logs/ に出力）
_LOG_DIR = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_log_file = os.path.join(_LOG_DIR, 'oh_cal_import_db_sc.log')

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


# 引数の解析
for arg in sys.argv[1:]:
    if arg.startswith("month="):
        try:
            loop_count = int(arg.split("=")[1])
        except ValueError:
            print("monthパラメータは整数で指定してください。")
            sys.exit(1)
    elif arg.startswith("branch="):
        try:
            branch_index = int(arg.split("=")[1])
        except ValueError:
            print("branchパラメータは整数で指定してください。")
            sys.exit(1)

shop_id = BRANCH_MAP.get(branch_index, 107859)
print(f"月遡り回数: {loop_count}, 支店インデックス: {branch_index}")



def format_datetime(iso_datetime):
    """ISO 8601 フォーマットを MySQL の DATETIME フォーマットに変換"""
    if not iso_datetime:
        return None
    try:
        dt = parser.isoparse(iso_datetime)  # ISO 8601 文字列をパース
        return dt.strftime("%Y-%m-%d %H:%M:%S")  # MySQL の DATETIME フォーマットへ変換
    except Exception as e:
        log_and_print(f"日時変換エラー: {iso_datetime} - {e}")
        return None

def log_and_print(message):
    """メッセージをログファイルとコンソールに出力"""
    print(message)
    logging.info(message)

def login(driver):
    """ログイン処理"""
    log_and_print("ログイン処理を開始します。")
    driver.get('https://admin.onehomeplus.jp/shop/login')

    email = os.getenv("OH_EMAIL")
    password = os.getenv("OH_PASSWORD")

    inputs = WebDriverWait(driver, 10).until(
        EC.presence_of_all_elements_located((By.CLASS_NAME, 'c-input'))
    )
    inputs[0].send_keys(email)
    inputs[1].send_keys(password)

    login_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CLASS_NAME, 'c-btn-add-a'))
    )
    login_button.click()

    # ログイン完了をURLで検知（/shop/login から離れるまで待機）
    WebDriverWait(driver, 15).until(
        lambda d: '/shop/login' not in d.current_url
    )
    update_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # branch_index に基づいて支店切り替え（None の場合はデフォルト 0）
    if branch_index is not None:
        switch_account(driver, branch_index)
    else:
        switch_account(driver, 0)
  
    log_and_print("ログイン処理が完了しました。")
    
    
    return update_dt

def wait_for_xhr(driver, url_keyword, timeout=15):
    """url_keywordを含むXHRレスポンスが来るまでポーリング待機"""
    start = time.time()
    while time.time() - start < timeout:
        for request in reversed(driver.requests):
            if request.response and url_keyword in request.url:
                try:
                    return json.loads(request.response.body.decode('utf-8'))
                except Exception:
                    pass
        time.sleep(0.3)
    return None

def switch_account(driver, option_index):
    """アカウントを切り替える"""
    select_element = driver.find_element(By.XPATH, '/html/body/div[1]/div/div/header/div/div[1]/div/select')
    select = Select(select_element)
    select.select_by_index(option_index)

    log_and_print(f"アカウントが切り替えられました: オプション {option_index}")
    # ローディングが消えるまで待機（固定sleepより高速）
    try:
        WebDriverWait(driver, 10).until(
            EC.invisibility_of_element_located((By.ID, "loadingOverlay"))
        )
    except Exception:
        time.sleep(2)

def upsert_reservation_data(cursor, reservation, update_dt):
    
    # 対象日判定用
    # end_dt = reservation.get("end_datetime")
    # end_date_str = ""
    # if end_dt:
    #     if isinstance(end_dt, str):
    #         end_date_str = end_dt[:10]  # "2025-04-12 15:00:00.000" → "2025-04-12"
    #     elif isinstance(end_dt, datetime):
    #         end_date_str = end_dt.strftime("%Y-%m-%d")

    # if end_date_str == "2025-04-12":
    #     log_and_print(f"[DEBUG] 対象データ: {reservation}")
    #     log_and_print(f"[DEBUG] update_dt: {update_dt}")

    """予約データをUPSERT"""
    query = """
    INSERT INTO reservation (
      id, shop_id, category_id, user_id, pet_id, web_reservation_initial, 
      web_reservation, reservation_no, start_datetime, end_datetime, 
      status, payment, users_memo, memo, unit_id,designate, is_first_reservation, update_dt
    )VALUES (
      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,%s, %s, %s, %s, %s, %s
    )ON DUPLICATE KEY UPDATE
      shop_id=VALUES(shop_id),
      category_id=VALUES(category_id),
      user_id=VALUES(user_id),
      pet_id=VALUES(pet_id),
      web_reservation_initial=VALUES(web_reservation_initial),
      web_reservation=VALUES(web_reservation),
      reservation_no=VALUES(reservation_no),
      start_datetime=VALUES(start_datetime),
      end_datetime=VALUES(end_datetime),
      status=VALUES(status),
      payment=VALUES(payment),
      users_memo=VALUES(users_memo),
      memo=VALUES(memo),
      unit_id=VALUES(unit_id),
      designate=VALUES(designate),
      is_first_reservation=VALUES(is_first_reservation),
      update_dt=VALUES(update_dt)
    """
    # 追加：クエリとパラメータをログ出力
    #log_and_print(f"実行するクエリ: {query}")
    #log_and_print(f"パラメータ: {reservation}")

    cursor.execute(query, (
        reservation.get("id"),
        reservation.get("shop_id"),
        reservation.get("category_id"),
        reservation.get("user_id"),
        reservation.get("pet_id"),
        reservation.get("web_reservation_initial"),
        reservation.get("web_reservation"),
        reservation.get("reservation_no"),
        format_datetime(reservation.get("start_datetime")),  # 🔥 ここを修正
        format_datetime(reservation.get("end_datetime")),    # 🔥 ここを修正
        reservation.get("status"),
        reservation.get("payment"),
        reservation.get("users_memo"),
        reservation.get("memo"),
        reservation.get("unit_id"),
        reservation.get("designate"),
        reservation.get("is_first_reservation"),
        update_dt
    ))

def upsert_reservation_details(cursor, details, parent_id,update_dt):
    """予約詳細データをUPSERT"""
    query = """
    INSERT INTO reservation_details (
      reservation_id,
      commodity_id_ref,
      commodity_type,
      pet_breed_id, 
      volume,
      commodity_id,
      adjusted_price,
      commodity_name,
      commodity_price,
      discount_id,
      update_dt
    )VALUES (
      %s,
      %s,
      %s,
      %s,
      %s,
      %s,
      %s,
      %s,
      %s,
      %s,
      %s
    ) ON DUPLICATE KEY UPDATE
        commodity_type=VALUES(commodity_type),
        commodity_id_ref=VALUES(commodity_id_ref),
        pet_breed_id=VALUES(pet_breed_id),
        volume=VALUES(volume),
        adjusted_price=VALUES(adjusted_price),
        commodity_name=VALUES(commodity_name),
        commodity_price=VALUES(commodity_price),
        discount_id=VALUES(discount_id),
        update_dt=VALUES(update_dt)
    """
    # 追加：クエリとパラメータをログ出力
    #log_and_print(f"実行するクエリ: {query}")
    #log_and_print(f"パラメータ: {details}")

    for detail in details:
        discount_id = detail.get("discount", {}).get("id", None)
        cursor.execute(query, (
          parent_id,
          detail.get("id"),
          detail.get("commodity_type"),
          detail.get("pet_breed_id"),
          detail.get("volume"),
          detail.get("commodity_id"),
          detail.get("adjusted_price"),
          detail.get("commodity", {}).get("name"),
          detail.get("commodity", {}).get("price"),
          discount_id,
          update_dt
        ))

def get_current_calendar_month(driver):
    """
    現在のカレンダーの表示年月を取得
    """
    try:
        # XPathを使用して要素を取得
        title_element = driver.find_element(By.XPATH, '//*[@id="fc-dom-1"]')
        calendar_month = title_element.text
        log_and_print(f"現在表示中の年月: {calendar_month}")
        return calendar_month
    except Exception as e:
        log_and_print(f"年月取得エラー: {e}")
        return None
def extract_date_from_request_url(driver):
    """リクエストURLからfromおよびtoパラメータを抽出（最新のリクエストを優先）"""
    for request in reversed(driver.requests):  # 最新のリクエストを優先
        if request.response and 'https://api.onehomeplus.jp/api/v1/salon/reservations' in request.url:
            try:
                # URLからfromとtoの値を正規表現で取得
                match = re.search(r'from=([^&]*)&to=([^&]*)', request.url)
                if match:
                    from_date = match.group(1)
                    to_date = match.group(2)
                    print(f"from: {from_date}, to: {to_date}")
                    return from_date, to_date
            except Exception as e:
                log_and_print(f"リクエストURL解析エラー: {e}")
    log_and_print("リクエスト内にfrom/toパラメータが見つかりませんでした")
    return None, None

def extract_reservation_id(url):
    """URLからreservation_idを抽出"""
    import re
    match = re.search(r"reservation_id=(\d+)", url)
    return match.group(1) if match else None

def fetch_xhr_response(driver, reservation_id):
    """XHRリクエストのレスポンスを取得"""
    for request in driver.requests:
        if request.response and reservation_id in request.url:
            try:
                response_body = request.response.body.decode('utf-8')
                #log_and_print(f"取得したレスポンス: {response_body}")  # レスポンス内容をログに出力
                return json.loads(response_body)
            except Exception as e:
                log_and_print(f"XHRレスポンス解析エラー: {e}")
                return None
    log_and_print(f"XHRリクエストが見つかりませんでした: reservation_id={reservation_id}")
    return None

def save_reservation_data_to_mysql(connection, reservation_json, update_dt):
    """MySQLにデータを保存"""
    cursor = connection.cursor()
    try:
        #log_and_print("予約データを保存中...")
        upsert_reservation_data(cursor, reservation_json.get("reservation", {}), update_dt)
        connection.commit()
        #log_and_print("予約詳細データを保存中...")
        upsert_reservation_details(cursor, reservation_json.get("reservation", {}).get("reservation_details", []), reservation_json.get("reservation", {}).get("id"),update_dt)
        connection.commit()
    except Exception as err:
        connection.rollback()
        log_and_print(f"MySQLエラー: {err}")
    finally:
        cursor.close()

def nullify_update_dt(cursor, from_date, to_date,shop_id):
    """指定された期間内のupdate_dtをNULLに更新"""
    # reservation.update_dt を NULL に更新
    query_reservation = """
    UPDATE reservation
    SET update_dt = NULL
    WHERE (start_datetime BETWEEN %s AND %s) AND shop_id=%s
    """
    cursor.execute(query_reservation, (from_date, to_date,shop_id))
    log_and_print(f"reservation.update_dt を NULL に更新しました: from={from_date}, to={to_date}, shop_id={shop_id}")

    # reservation_detail.update_dt を NULL に更新
    query_reservation_detail = """
        UPDATE reservation_details
        SET update_dt = NULL
        WHERE reservation_id IN (
          SELECT id FROM reservation
          WHERE (start_datetime BETWEEN %s AND %s)
          AND shop_id = %s
        )
    """
    cursor.execute(query_reservation_detail, (from_date, to_date , shop_id))
    log_and_print(f"reservation_details.update_dt を NULL に更新しました: from={from_date}, to={to_date}, shop_id={shop_id}")

def scrape_calendar(driver, connection, update_dt, shop_id):
    """カレンダーの予約情報を取得"""
    log_and_print("カレンダーのスクレイピングを開始します。")
    driver.get('https://admin.onehomeplus.jp/shop/calendar/calendar')
    time.sleep(5)
    # ローディングオーバーレイが消えるのを待つ
    WebDriverWait(driver, 30).until(
        EC.invisibility_of_element_located((By.ID, "loadingOverlay"))
    )

    # ページ読み込み後の年月を取得
    current_month = get_current_calendar_month(driver)
    log_and_print(f"初期表示中の年月: {current_month}")

    # ページ読み込み後の日付範囲を取得
    from_date, to_date = extract_date_from_request_url(driver)
    if from_date and to_date:
        log_and_print(f"初期日付範囲: from={from_date}, to={to_date}")

        # 取得した日付範囲でupdate_dtをNULLにする
        #cursor = connection.cursor()
        #nullify_update_dt(cursor, from_date, to_date)
        #connection.commit()

    if loop_count > 0:
        for i in tqdm(range(loop_count, 0, -1), desc="月を遡り中", unit="月", file=sys.stderr):
            prev_from, _ = extract_date_from_request_url(driver)
            del driver.requests  # 古いリクエストをクリア

            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="__layout"]/div/main/main/div/div[2]/div/div[2]/div/div[1]/div[2]/div/button[1]'))
            ).click()

            # 新しいXHRが届くまでポーリング（sleep(10)を廃止）
            start = time.time()
            while time.time() - start < 20:
                new_from, new_to = extract_date_from_request_url(driver)
                if new_from and new_from != prev_from:
                    break
                time.sleep(0.5)

            current_month = get_current_calendar_month(driver)
            log_and_print(f"操作後の表示年月: {current_month}")
            from_date, to_date = extract_date_from_request_url(driver)
            if from_date and to_date:
                log_and_print(f"操作後の日付範囲: from={from_date}, to={to_date}")

    # 取得した日付範囲でupdate_dtをNULLにする
    cursor = connection.cursor()
    nullify_update_dt(cursor, from_date, to_date, shop_id)
    connection.commit()

    # イベントの取得
    events = WebDriverWait(driver, 10).until(
        EC.presence_of_all_elements_located(
            (By.XPATH, '/html/body/div[1]/div/div/main/main/div/div[2]/div/div[2]/div/div[3]/div/table/tbody/tr/td/div/div/div/table//a')
        )
    )

    # hrefからreservation_idを一括収集（クリック不要）
    event_hrefs = []
    for event in events:
        href = event.get_attribute('href') or ''
        rid = extract_reservation_id(href)
        if rid:
            event_hrefs.append((rid, href))

    log_and_print(f"取得したイベント数: {len(event_hrefs)}")

    # 各詳細ページへ直接ナビゲート → XHRをポーリング待機（sleep(2)を廃止）
    for idx, (rid, href) in enumerate(tqdm(event_hrefs, desc="イベント取得", unit="件", file=sys.stderr)):
        del driver.requests
        driver.get(href)

        start = time.time()
        reservation_data = None
        while time.time() - start < 10:
            reservation_data = fetch_xhr_response(driver, rid)
            if reservation_data:
                break
            time.sleep(0.3)

        if reservation_data:
            save_reservation_data_to_mysql(connection, reservation_data, update_dt)

    log_and_print("カレンダーのスクレイピングが完了しました。")

def main():
    """メイン処理"""
    logging.info("=" * 60)
    logging.info(f"処理開始 branch={branch_index} month={loop_count}")
    start_time = datetime.now()

    try:
        db_connector = MySQLSSHConnector()
        connection = db_connector.connection

        try:
            options = {"disable_encoding": True}
            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--log-level=3")
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                seleniumwire_options=options,
                options=chrome_options
            )

            try:
                update_dt = login(driver)
                scrape_calendar(driver, connection, update_dt, shop_id)
            finally:
                driver.quit()
        finally:
            db_connector.close()
    except Exception as e:
        logging.error(f"致命的エラー: {e}", exc_info=True)
        raise
    finally:
        elapsed = datetime.now() - start_time
        logging.info(f"処理終了 経過時間: {elapsed}")
        logging.info("=" * 60)

if __name__ == "__main__":
    main()
