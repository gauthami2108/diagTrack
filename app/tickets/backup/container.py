"""
The encrypted file format used for every DiagTrack backup (.dtbak) and every stored attachment (.dta).

    DTBAK\\x01 | 4-byte header length | header (JSON) | chunk | chunk | ... | final chunk

The header is readable without the passphrase and holds only a summary that is safe to show:
when, what kind, which version, how many tickets. Everything else (the database, attachments,
keys) is in the chunks, encrypted with AES-256-GCM. The key comes from the backup passphrase
through scrypt. Every chunk is authenticated together with the header, its position and whether it
is the last one, so a changed, reordered or cut-off file is detected and refused.
"""
import base64
import hashlib
import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"DTBAK\x01"
CHUNK = 1024 * 1024
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
_keys = {}


class BadPassphrase(Exception):
    pass


class Damaged(Exception):
    pass


def _b64e(raw):
    return base64.b64encode(raw).decode("ascii")


def _b64d(text):
    return base64.b64decode(text)


def new_salt():
    return os.urandom(16)


def derive_key(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P):
    if not passphrase:
        raise BadPassphrase("No backup passphrase is set. Run setup and choose Repair to set it.")
    cache_key = (hashlib.sha256(passphrase.encode("utf-8")).hexdigest(), salt, n, r, p)
    if cache_key not in _keys:
        _keys[cache_key] = Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))
    return _keys[cache_key]


class Writer:
    """File-like object: write plain bytes, they land encrypted in `fileobj`."""

    def __init__(self, fileobj, passphrase, salt, summary):
        self.f = fileobj
        self.key = derive_key(passphrase, salt)
        self.prefix = os.urandom(8)
        header = {"format": 1, "cipher": "AES-256-GCM", "chunk": CHUNK, "nonce": _b64e(self.prefix),
                  "kdf": {"name": "scrypt", "salt": _b64e(salt), "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
                  "summary": summary}
        self.header = json.dumps(header, sort_keys=True).encode("utf-8")
        self.aad_base = hashlib.sha256(self.header).digest()
        self.f.write(MAGIC + struct.pack(">I", len(self.header)) + self.header)
        self.buf = bytearray()
        self.index = 0
        self.closed = False

    def _seal(self, data, final):
        nonce = self.prefix + struct.pack(">I", self.index)
        aad = self.aad_base + struct.pack(">IB", self.index, 1 if final else 0)
        sealed = AESGCM(self.key).encrypt(nonce, bytes(data), aad)
        self.f.write(struct.pack(">I", len(sealed)) + sealed)
        self.index += 1

    def write(self, data):
        self.buf += data
        while len(self.buf) > CHUNK:
            self._seal(self.buf[:CHUNK], False)
            del self.buf[:CHUNK]
        return len(data)

    def flush(self):
        pass

    def close(self):
        if not self.closed:
            self._seal(self.buf, True)
            self.buf = bytearray()
            self.closed = True


def read_header(fileobj):
    magic = fileobj.read(len(MAGIC))
    if magic != MAGIC:
        raise Damaged("This is not a DiagTrack backup file.")
    (length,) = struct.unpack(">I", fileobj.read(4))
    if length > 1024 * 1024:
        raise Damaged("The backup file's header is damaged.")
    raw = fileobj.read(length)
    try:
        return raw, json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise Damaged("The backup file's header is damaged.") from exc


def summary_of(path):
    """The readable summary of a backup, without the passphrase."""
    with open(path, "rb") as f:
        return read_header(f)[1].get("summary", {})


class Reader:
    """File-like object: read decrypted bytes from an encrypted file."""

    def __init__(self, fileobj, passphrase):
        self.f = fileobj
        raw, header = read_header(fileobj)
        if header.get("format") != 1:
            raise Damaged("This backup was made by a newer DiagTrack. Install the newer version first.")
        kdf = header["kdf"]
        self.header = header
        self.summary = header.get("summary", {})
        self.key = derive_key(passphrase, _b64d(kdf["salt"]), kdf["n"], kdf["r"], kdf["p"])
        self.prefix = _b64d(header["nonce"])
        self.aad_base = hashlib.sha256(raw).digest()
        self.index = 0
        self.buf = b""
        self.done = False

    def _next(self):
        head = self.f.read(4)
        if len(head) < 4:
            raise Damaged("The backup file is incomplete: it ends before its last part.")
        (length,) = struct.unpack(">I", head)
        sealed = self.f.read(length)
        if len(sealed) < length:
            raise Damaged("The backup file is incomplete: it ends before its last part.")
        nonce = self.prefix + struct.pack(">I", self.index)
        for final in (0, 1):
            aad = self.aad_base + struct.pack(">IB", self.index, final)
            try:
                plain = AESGCM(self.key).decrypt(nonce, sealed, aad)
            except InvalidTag:
                continue
            self.index += 1
            self.done = bool(final)
            return plain
        if self.index == 0:
            raise BadPassphrase("The backup passphrase is wrong for this file.")
        raise Damaged(f"The backup file is damaged (part {self.index + 1} failed its check).")

    def read(self, size=-1):
        while not self.done and (size < 0 or len(self.buf) < size):
            self.buf += self._next()
        if size < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[:size], self.buf[size:]
        return out

    def finish(self):
        """Read to the end, proving the whole file is intact."""
        while not self.done:
            self._next()
        if self.f.read(1):
            raise Damaged("The backup file has unexpected data after its end.")


def encrypt_file(src, dst, passphrase, salt, summary):
    """Encrypt one file (an attachment) and return the SHA-256 of its plain contents."""
    digest = hashlib.sha256()
    tmp = f"{dst}.part"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        w = Writer(fout, passphrase, salt, summary)
        for block in iter(lambda: fin.read(CHUNK), b""):
            digest.update(block)
            w.write(block)
        w.close()
    os.replace(tmp, dst)
    return digest.hexdigest()


def decrypt_file(src, dst, passphrase):
    """Decrypt one stored attachment to dst. Returns the SHA-256 of what was written."""
    digest = hashlib.sha256()
    tmp = f"{dst}.part"
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        r = Reader(fin, passphrase)
        while True:
            block = r.read(CHUNK)
            if not block:
                break
            digest.update(block)
            fout.write(block)
        r.finish()
    os.replace(tmp, dst)
    return digest.hexdigest()
