"""主揪 Nidin 登入密碼的對稱加密。

金鑰優先讀 NIDIN_CREDENTIAL_KEY；沒設就在資料庫旁產生一個 0600 的金鑰檔。
金鑰檔與資料庫放在同一個 volume，所以擋得住的是「只外流資料庫」（備份、
複製 cart.db 出去除錯），擋不住整個 volume 外流。要更強就改用環境變數。
"""

import hashlib
import hmac
import os
from pathlib import Path

from cryptography.fernet import Fernet


KEY_ENV = "NIDIN_CREDENTIAL_KEY"
KEY_FILENAME = "nidin-credential.key"


def _load_key(data_dir: Path) -> bytes:
    env_key = os.getenv(KEY_ENV, "").strip()
    if env_key:
        return env_key.encode()
    path = data_dir / KEY_FILENAME
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        pass
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # 另一個 worker 剛好搶先寫入，用它的，否則兩邊加密的資料互相解不開
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def load_fernet(data_dir: Path) -> Fernet:
    return Fernet(_load_key(data_dir))


def derive_secret(data_dir: Path, purpose: str) -> bytes:
    """同一把金鑰衍生出不同用途的密鑰，彼此無法互推。"""
    return hmac.new(_load_key(data_dir), purpose.encode(), hashlib.sha256).digest()
