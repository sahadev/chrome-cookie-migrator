#!/usr/bin/env python3
"""
Chrome Cookie Migrator for macOS
Export all Chrome cookies from one Mac and import them into another.
"""

import argparse
import base64
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

CHROME_USER_DATA = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome"
)
AES_IV = b" " * 16
AES_BLOCK_BITS = 128
PBKDF2_SALT = b"saltysalt"
PBKDF2_ITERATIONS = 1003
PBKDF2_KEY_LEN = 16
COOKIE_VERSION_PREFIX = b"v10"
HOST_KEY_HASH_LEN = 32


def get_chrome_safe_storage_password():
    """Retrieve 'Chrome Safe Storage' password from macOS Keychain."""
    try:
        password = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            stderr=subprocess.DEVNULL,
        )
        return password.strip()
    except subprocess.CalledProcessError:
        print("Error: Could not retrieve Chrome Safe Storage password from Keychain.")
        print("Make sure Chrome has been run at least once on this machine.")
        sys.exit(1)


def derive_aes_key(password: bytes) -> bytes:
    """Derive AES-128 key from Chrome Safe Storage password via PBKDF2."""
    return hashlib.pbkdf2_hmac(
        "sha1", password, PBKDF2_SALT, PBKDF2_ITERATIONS, dklen=PBKDF2_KEY_LEN
    )


def decrypt_value(encrypted_value: bytes, key: bytes) -> str:
    """Decrypt a single cookie's encrypted_value using AES-128-CBC.

    Chromium prepends a 32-byte SHA-256 hash of the host_key to the plaintext
    before encrypting. After decryption we strip that prefix to get the real value.
    """
    if not encrypted_value:
        return ""

    if encrypted_value[:3] == COOKIE_VERSION_PREFIX:
        encrypted_value = encrypted_value[3:]
    else:
        try:
            return encrypted_value.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    cipher = Cipher(algorithms.AES(key), modes.CBC(AES_IV))
    decryptor = cipher.decryptor()
    decrypted_padded = decryptor.update(encrypted_value) + decryptor.finalize()

    pad_len = decrypted_padded[-1]
    if pad_len < 1 or pad_len > 16:
        return ""
    decrypted = decrypted_padded[:-pad_len]

    if len(decrypted) < HOST_KEY_HASH_LEN:
        return ""

    return decrypted[HOST_KEY_HASH_LEN:].decode("utf-8")


def encrypt_value(plain_value: str, host_key: str, key: bytes) -> bytes:
    """Encrypt a cookie value using AES-128-CBC and prepend version prefix.

    Chromium expects plaintext = SHA-256(host_key) + actual_value before encryption.
    """
    if not plain_value:
        return b""

    host_hash = hashlib.sha256(host_key.encode("utf-8")).digest()
    payload = host_hash + plain_value.encode("utf-8")

    padder = PKCS7(AES_BLOCK_BITS).padder()
    padded = padder.update(payload) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(AES_IV))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    return COOKIE_VERSION_PREFIX + encrypted


def get_cookie_db_path(profile: str = "Default") -> str:
    """Get the full path to the Chrome Cookies SQLite database."""
    path = os.path.join(CHROME_USER_DATA, profile, "Cookies")
    if not os.path.exists(path):
        print(f"Error: Cookie database not found at: {path}")
        print(f"Available profiles:")
        try:
            for entry in sorted(os.listdir(CHROME_USER_DATA)):
                candidate = os.path.join(CHROME_USER_DATA, entry, "Cookies")
                if os.path.exists(candidate):
                    print(f"  - {entry}")
        except OSError:
            pass
        sys.exit(1)
    return path


def is_chrome_running() -> bool:
    """Check if Chrome is currently running."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Google Chrome"],
            capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def warn_if_chrome_running():
    """Print a warning and exit if Chrome is running."""
    if is_chrome_running():
        print("Error: Google Chrome is currently running.")
        print("Please quit Chrome completely before running this tool.")
        print("  (Cmd+Q or Chrome menu -> Quit Google Chrome)")
        sys.exit(1)


def get_db_columns(db_path: str) -> list[str]:
    """Read actual column names from the cookies table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(cookies)")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    return columns


def do_export(args):
    """Export all Chrome cookies to a JSON file."""
    warn_if_chrome_running()

    db_path = get_cookie_db_path(args.profile)
    password = get_chrome_safe_storage_password()
    key = derive_aes_key(password)

    tmp_db = tempfile.mktemp(suffix=".sqlite")
    shutil.copy2(db_path, tmp_db)

    try:
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row

        actual_columns = get_db_columns(tmp_db)
        select_cols = ", ".join(actual_columns)

        cursor = conn.execute(f"SELECT {select_cols} FROM cookies")
        rows = cursor.fetchall()

        cookies = []
        failed = 0
        for row in rows:
            row_dict = dict(row)

            encrypted = row_dict.pop("encrypted_value", b"")
            plain_value = row_dict.get("value", "")

            if encrypted and not plain_value:
                try:
                    plain_value = decrypt_value(encrypted, key)
                except Exception:
                    failed += 1
                    plain_value = ""

            row_dict["value"] = plain_value
            cookies.append(row_dict)

        conn.close()

        output = {
            "exported_at": datetime.now().isoformat(),
            "profile": args.profile,
            "cookie_count": len(cookies),
            "columns": [c for c in actual_columns if c != "encrypted_value"],
            "cookies": cookies,
        }

        output_path = args.output
        if args.encrypt:
            output_path = export_encrypted(output, output_path)
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"Exported {len(cookies)} cookies to: {output_path}")
        if failed:
            print(f"  ({failed} cookies failed to decrypt, exported with empty value)")

    finally:
        os.unlink(tmp_db)


def do_import(args):
    """Import cookies from a JSON file into Chrome."""
    warn_if_chrome_running()

    input_path = args.input
    if args.encrypt:
        data = import_encrypted(input_path)
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    cookies = data["cookies"]
    if not cookies:
        print("No cookies found in backup file.")
        return

    if args.domains:
        domain_filter = set(args.domains.split(","))
        cookies = [
            c
            for c in cookies
            if any(
                c.get("host_key", "").endswith(d.strip()) for d in domain_filter
            )
        ]
        print(f"Filtered to {len(cookies)} cookies matching domains: {args.domains}")

    db_path = get_cookie_db_path(args.profile)

    backup_path = db_path + f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    print(f"Backed up original database to: {backup_path}")

    password = get_chrome_safe_storage_password()
    key = derive_aes_key(password)

    actual_columns = get_db_columns(db_path)

    insert_columns = [c for c in actual_columns if c != "encrypted_value"]
    available_in_backup = set(cookies[0].keys()) if cookies else set()

    columns_to_insert = [c for c in insert_columns if c in available_in_backup]
    columns_to_insert_with_enc = columns_to_insert + ["encrypted_value"]

    placeholders = ", ".join(["?"] * len(columns_to_insert_with_enc))
    col_names = ", ".join(columns_to_insert_with_enc)

    conn = sqlite3.connect(db_path)
    imported = 0
    skipped = 0

    for cookie in cookies:
        plain_value = cookie.get("value", "")
        host_key = cookie.get("host_key", "")
        encrypted = encrypt_value(plain_value, host_key, key) if plain_value else b""

        values = []
        for col in columns_to_insert:
            val = cookie.get(col, "" if col in ("value", "host_key", "name", "path") else 0)
            values.append(val)
        values.append(encrypted)

        cookie["value"] = ""

        try:
            conn.execute(
                f"INSERT OR REPLACE INTO cookies ({col_names}) VALUES ({placeholders})",
                values,
            )
            imported += 1
        except sqlite3.Error as e:
            skipped += 1
            if skipped <= 3:
                print(f"  Warning: skipped cookie {cookie.get('host_key')}/{cookie.get('name')}: {e}")

    conn.commit()
    conn.close()

    print(f"Imported {imported} cookies into profile '{args.profile}'.")
    if skipped:
        print(f"  ({skipped} cookies skipped due to errors)")
    print("Restart Chrome to use the imported cookies.")


def export_encrypted(data: dict, output_path: str) -> str:
    """Encrypt the JSON backup with a user-provided password."""
    password = getpass.getpass("Enter encryption password for backup: ")
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Error: Passwords do not match.")
        sys.exit(1)

    salt = os.urandom(16)
    enc_key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000, dklen=32)
    iv = os.urandom(16)

    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")

    padder = PKCS7(AES_BLOCK_BITS).padder()
    padded = padder.update(json_bytes) + padder.finalize()

    cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    if not output_path.endswith(".enc"):
        output_path += ".enc"

    with open(output_path, "wb") as f:
        f.write(salt + iv + ciphertext)

    return output_path


def import_encrypted(input_path: str) -> dict:
    """Decrypt an encrypted backup file."""
    password = getpass.getpass("Enter decryption password: ")

    with open(input_path, "rb") as f:
        raw = f.read()

    salt = raw[:16]
    iv = raw[16:32]
    ciphertext = raw[32:]

    enc_key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000, dklen=32)

    cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
    decryptor = cipher.decryptor()

    try:
        decrypted_padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = PKCS7(AES_BLOCK_BITS).unpadder()
        decrypted = unpadder.update(decrypted_padded) + unpadder.finalize()
    except Exception:
        print("Error: Wrong password or corrupted file.")
        sys.exit(1)

    return json.loads(decrypted.decode("utf-8"))


def list_profiles():
    """List available Chrome profiles."""
    print("Available Chrome profiles:")
    try:
        for entry in sorted(os.listdir(CHROME_USER_DATA)):
            candidate = os.path.join(CHROME_USER_DATA, entry, "Cookies")
            if os.path.exists(candidate):
                db_size = os.path.getsize(candidate)
                print(f"  - {entry}  ({db_size // 1024} KB)")
    except OSError as e:
        print(f"Error reading Chrome data: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Chrome Cookie Migrator for macOS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s export -o cookies.json
  %(prog)s export -o cookies.enc --encrypt
  %(prog)s import -i cookies.json
  %(prog)s import -i cookies.json --domains "github.com,google.com"
  %(prog)s list-profiles
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # export
    p_export = subparsers.add_parser("export", help="Export all cookies to JSON")
    p_export.add_argument("-o", "--output", default="cookies_backup.json", help="Output file path")
    p_export.add_argument("--profile", default="Default", help="Chrome profile name (default: Default)")
    p_export.add_argument("--encrypt", action="store_true", help="Encrypt the backup with a password")

    # import
    p_import = subparsers.add_parser("import", help="Import cookies from JSON")
    p_import.add_argument("-i", "--input", required=True, help="Input backup file path")
    p_import.add_argument("--profile", default="Default", help="Chrome profile name (default: Default)")
    p_import.add_argument("--domains", help="Only import specific domains (comma-separated)")
    p_import.add_argument("--encrypt", action="store_true", help="Input file is encrypted")

    # list-profiles
    subparsers.add_parser("list-profiles", help="List available Chrome profiles")

    args = parser.parse_args()

    if sys.platform != "darwin":
        print("Error: This tool only supports macOS.")
        sys.exit(1)

    if args.command == "export":
        do_export(args)
    elif args.command == "import":
        do_import(args)
    elif args.command == "list-profiles":
        list_profiles()


if __name__ == "__main__":
    main()
