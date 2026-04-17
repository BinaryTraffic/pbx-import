# Windows専用: 完全な分離のためにプロセスフラグを使う
import subprocess
import time
from datetime import datetime
import os
import sys
from tqdm import tqdm
CREATE_NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0

branch_indices = [0, 1, 2]
_BASE = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(_BASE, '..', 'logs')
os.makedirs(log_dir, exist_ok=True)

def run_silently_to_log(script_name, args_per_branch):
    for branch in tqdm(branch_indices, desc=f"{script_name} 実行中", unit="支店"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = script_name.replace('.exe', '').replace(' ', '_')
        log_filename = f"{base_name}_branch{branch}_{timestamp}.log"
        log_path = os.path.join(log_dir, log_filename)

        with open(log_path, "w", encoding="utf-8-sig") as logf:
            process = subprocess.Popen(
                [script_name] + args_per_branch + [f"branch={branch}"],
                stdout=logf,
                stderr=logf,
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=False
            )
            process.wait()
        time.sleep(1)

# === ステップ①: oh_cal_import_db_sc.exe を支店ごとに順次実行 ===
run_silently_to_log("oh_cal_import_db_sc.exe", ["month=0"])

# === ステップ②: oh_u_p_import copy.exe を支店ごとに順次実行 ===
run_silently_to_log("oh_u_p_import.exe", [])

# === ステップ③: 完了メッセージ ===
print("✅ すべての支店・スクリプトの処理が完了しました。")
