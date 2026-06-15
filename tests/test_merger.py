"""Tests for m3u8_downloader.merger module."""

import os
import tempfile

import pytest

from m3u8_downloader.parser import M3U8Key, M3U8Segment
from m3u8_downloader.merger import (
    _decrypt_segment,
    decrypt_and_save_segment,
    merge_ts_files_binary,
    decrypt_segments,
)


# ---------------------------------------------------------------------------
# _decrypt_segment (AES-128-CBC)
# ---------------------------------------------------------------------------

class TestDecryptSegment:
    """Tests for _decrypt_segment function."""

    def test_decrypt_with_known_key_and_iv(self):
        """Encrypt then decrypt round-trip test."""
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        key = b"\x00" * 16  # 16-byte key
        iv = b"\x00" * 16
        plaintext = b"Hello, AES-128! " * 4  # Must be multiple of 16 for AES

        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(plaintext, AES.block_size))

        decrypted = _decrypt_segment(encrypted, key, iv)
        assert decrypted == plaintext

    def test_decrypt_with_different_key(self):
        """Test with a non-zero key."""
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        key = bytes(range(16))
        iv = bytes(range(16))
        plaintext = b"Test data here!" * 8

        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(plaintext, AES.block_size))

        decrypted = _decrypt_segment(encrypted, key, iv)
        assert decrypted == plaintext

    def test_decrypt_default_iv(self):
        """Test decryption with IV=None (should use zero IV)."""
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        key = b"mysecretkey12345"
        iv = None  # Should default to b"\x00" * 16
        plaintext = b"Some test data!" * 10

        cipher = AES.new(key, AES.MODE_CBC, b"\x00" * 16)
        encrypted = cipher.encrypt(pad(plaintext, AES.block_size))

        decrypted = _decrypt_segment(encrypted, key, iv)
        assert decrypted == plaintext

    def test_decrypt_wrong_key_fails(self):
        """Decrypting with wrong key should produce garbage, not crash."""
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        key = b"\x00" * 16
        wrong_key = b"\xff" * 16
        iv = b"\x00" * 16
        plaintext = b"Hello, World!!!" * 4

        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(plaintext, AES.block_size))

        # Should not crash, just return wrong data
        result = _decrypt_segment(encrypted, wrong_key, iv)
        assert result != plaintext  # Data is garbled, not equal


# ---------------------------------------------------------------------------
# decrypt_and_save_segment
# ---------------------------------------------------------------------------

class TestDecryptAndSaveSegment:
    """Tests for decrypt_and_save_segment function."""

    def test_decrypt_and_save(self):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        key = b"\x00" * 16
        iv = b"\x00" * 16
        plaintext = b"Segment data!!" * 4

        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(plaintext, AES.block_size))

        with tempfile.TemporaryDirectory() as tmpdir:
            enc_path = os.path.join(tmpdir, "encrypted.ts")
            dec_path = os.path.join(tmpdir, "decrypted.ts")

            with open(enc_path, "wb") as f:
                f.write(encrypted)

            decrypt_and_save_segment(enc_path, dec_path, key, iv)

            with open(dec_path, "rb") as f:
                result = f.read()

            assert result == plaintext


# ---------------------------------------------------------------------------
# merge_ts_files_binary
# ---------------------------------------------------------------------------

class TestMergeTsFilesBinary:
    """Tests for merge_ts_files_binary function."""

    def test_merge_two_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seg1 = os.path.join(tmpdir, "seg1.ts")
            seg2 = os.path.join(tmpdir, "seg2.ts")
            output = os.path.join(tmpdir, "merged.ts")

            with open(seg1, "wb") as f:
                f.write(b"AAAA")
            with open(seg2, "wb") as f:
                f.write(b"BBBB")

            merge_ts_files_binary([seg1, seg2], output)

            with open(output, "rb") as f:
                assert f.read() == b"AAAABBBB"

    def test_merge_single_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            seg1 = os.path.join(tmpdir, "seg1.ts")
            output = os.path.join(tmpdir, "merged.ts")

            with open(seg1, "wb") as f:
                f.write(b"DATA")

            merge_ts_files_binary([seg1], output)

            with open(output, "rb") as f:
                assert f.read() == b"DATA"

    def test_merge_empty_list_creates_empty_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "merged.ts")

            merge_ts_files_binary([], output)

            with open(output, "rb") as f:
                assert f.read() == b""

    def test_merge_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = os.path.join(tmpdir, "nonexistent.ts")
            output = os.path.join(tmpdir, "merged.ts")

            with pytest.raises(FileNotFoundError):
                merge_ts_files_binary([missing], output)


# ---------------------------------------------------------------------------
# decrypt_segments
# ---------------------------------------------------------------------------

class TestDecryptSegments:
    """Tests for decrypt_segments function."""

    def test_unencrypted_segments_returned_as_is(self):
        segments = [
            M3U8Segment(url="http://example.com/1.ts"),
            M3U8Segment(url="http://example.com/2.ts"),
        ]
        paths = ["/tmp/1.ts", "/tmp/2.ts"]

        result = decrypt_segments(segments, paths)
        assert result == paths

    def test_key_none_segments_returned_as_is(self):
        key = M3U8Key(method="NONE")
        segments = [
            M3U8Segment(url="http://example.com/1.ts", key=key),
        ]
        paths = ["/tmp/1.ts"]

        result = decrypt_segments(segments, paths)
        assert result == paths

    def test_encrypted_segment_key_not_downloaded_returns_original(self):
        """If key.key is None (not downloaded), should return original path with warning."""
        key = M3U8Key(method="AES-128", uri="https://example.com/key.php", key=None)
        segments = [
            M3U8Segment(url="http://example.com/1.ts", key=key),
        ]
        paths = ["/tmp/1.ts"]

        result = decrypt_segments(segments, paths)
        assert result == paths  # Should return original since key not available

    def test_encrypted_segment_decrypted(self):
        """Full round-trip: encrypt, save, decrypt via decrypt_segments."""
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        key_data = b"\x00" * 16
        iv_data = b"\x00" * 16
        plaintext = b"Test segment!!" * 4

        cipher = AES.new(key_data, AES.MODE_CBC, iv_data)
        encrypted = cipher.encrypt(pad(plaintext, AES.block_size))

        with tempfile.TemporaryDirectory() as tmpdir:
            enc_path = os.path.join(tmpdir, "seg_0.ts")

            with open(enc_path, "wb") as f:
                f.write(encrypted)

            key_obj = M3U8Key(method="AES-128", uri="https://example.com/key.php",
                              iv=iv_data, key=key_data)
            segments = [
                M3U8Segment(url="http://example.com/1.ts", key=key_obj),
            ]
            paths = [enc_path]

            result_paths = decrypt_segments(segments, paths)
            assert len(result_paths) == 1
            assert result_paths[0].endswith(".dec")

            with open(result_paths[0], "rb") as f:
                decrypted = f.read()

            assert decrypted == plaintext
