"""Tests fuer `AuthStore` - den Teil des Stores, der den Zugang verwaltet.

Die Kernfrage: haelt `setting` genau einen Passwort-Hash, und laesst sich
`session` so fuehren, dass eine abgelaufene Sitzung nicht mehr gilt und eine
geloeschte sofort weg ist?
"""

from __future__ import annotations

from loxmatter.model.store import Store


def test_password_hash_is_none_on_a_fresh_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.auth.password_hash() is None
    finally:
        store.close()


def test_set_password_hash_if_unset_wins_once_and_then_never_again(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.auth.set_password_hash_if_unset("erster") is True
        assert store.auth.set_password_hash_if_unset("zweiter") is False
        assert store.auth.password_hash() == "erster"
    finally:
        store.close()


def test_set_password_hash_replaces_an_existing_one(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.set_password_hash_if_unset("alt")
        store.auth.set_password_hash("neu")
        assert store.auth.password_hash() == "neu"
    finally:
        store.close()


def test_sessions_are_stored_read_extended_and_deleted(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.create_session("abc", created_at=100, expires_at=200)
        assert store.auth.session_expires_at("abc") == 200
        store.auth.extend_session("abc", expires_at=300)
        assert store.auth.session_expires_at("abc") == 300
        store.auth.delete_session("abc")
        assert store.auth.session_expires_at("abc") is None
    finally:
        store.close()


def test_unknown_session_has_no_expiry(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.auth.session_expires_at("gibt-es-nicht") is None
    finally:
        store.close()


def test_purge_removes_only_expired_sessions(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.create_session("alt", created_at=1, expires_at=100)
        store.auth.create_session("frisch", created_at=1, expires_at=500)
        store.auth.purge_expired_sessions(200)
        assert store.auth.session_expires_at("alt") is None
        assert store.auth.session_expires_at("frisch") == 500
    finally:
        store.close()


def test_reset_password_replaces_the_hash_and_clears_sessions(tmp_path):
    """Fund G: `loxmatter set-password` darf den neuen Hash und das Abmelden
    aller Sitzungen nicht als zwei getrennt committende Schritte absetzen -
    scheitert der zweite, gilt das neue Passwort, waehrend eine alte Sitzung
    weiterlaeuft. `reset_password` fasst beides in einer Transaktion
    zusammen; dieser Test prueft nur das sichtbare Ergebnis, nicht die
    Transaktionsgrenze selbst (die ist ohne einen fehlschlagenden zweiten
    Schritt nicht beobachtbar)."""
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.set_password_hash_if_unset("alt")
        store.auth.create_session("a", created_at=1, expires_at=500)
        store.auth.create_session("b", created_at=1, expires_at=500)

        store.auth.reset_password("neu")

        assert store.auth.password_hash() == "neu"
        assert store.auth.session_expires_at("a") is None
        assert store.auth.session_expires_at("b") is None
    finally:
        store.close()


def test_delete_all_sessions_leaves_the_password_untouched(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.set_password_hash_if_unset("hash")
        store.auth.create_session("a", created_at=1, expires_at=500)
        store.auth.create_session("b", created_at=1, expires_at=500)
        store.auth.delete_all_sessions()
        assert store.auth.session_expires_at("a") is None
        assert store.auth.session_expires_at("b") is None
        assert store.auth.password_hash() == "hash"
    finally:
        store.close()
