# Windows専用: 完全な分離のためにプロセスフラグを使う
import subprocess
import time
from datetime import datetime
import os
import sys
import io
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from tqdm import tqdm

_BASE = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(_BASE, '..', 'logs')
os.makedirs(log_dir, exist_ok=True)

# 自身がbeta版かどうかを実行ファイル名で判定
_exe_name = os.path.basename(sys.executable) if getattr(sys, 'frozen', False) else ''
_is_beta = '_beta' in _exe_name

def _to_exe(name):
    return name.replace('.exe', '_beta.exe') if _is_beta else name

# branch=N を argv から取得（なければ全支店）
_branch_arg = None
for _a in sys.argv[1:]:
    if _a.startswith('branch='):
        try:
            _branch_arg = int(_a.split('=')[1])
        except ValueError:
            pass

branch_indices = [_branch_arg] if _branch_arg is not None else [0, 1, 2]

# 1支店あたりに実行するスクリプトステップ
SCRIPT_STEPS = [
    (_to_exe("oh_cal_import_db_sc.exe"), ["month=0"], "カレンダー読み取り"),
    (_to_exe("oh_u_p_import.exe"),        [],          "ユーザー/ペット取込"),
]

def run_branch(branch):
    bar = tqdm(total=len(SCRIPT_STEPS), desc=f"支店{branch}", unit="ステップ", file=sys.stderr)
    for script_name, args, label in SCRIPT_STEPS:
        bar.set_description(label)
        bar.refresh()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = script_name.replace('.exe', '').replace(' ', '_')
        log_path = os.path.join(log_dir, f"{base_name}_branch{branch}_{timestamp}.log")
        with open(log_path, "w", encoding="utf-8-sig") as logf:
            process = subprocess.Popen(
                [script_name] + args + [f"branch={branch}"],
                stdout=logf,
                stderr=logf,
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=False
            )
            process.wait()
        bar.update(1)
        time.sleep(1)
    bar.close()

for branch in branch_indices:
    run_branch(branch)

print("✅ すべての支店・スクリプトの処理が完了しました。")
