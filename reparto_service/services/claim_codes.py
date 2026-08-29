"""Minting, normalising and hashing teacher-profile claim codes (`W1.4`).

A claim code is the credential a teacher presents to bind a profile to their
own account. It exists because the *accounts directory belongs to*
``fa-auth-m8`` *and is superuser-only by that service's own design*: a
department head cannot look a colleague's user id up, so nothing in this
service may be built around discovering one. The teacher's own token already
carries their id, so the only missing piece is proof that this teacher is the
person the head meant — which is exactly what a head-issued, single-use,
expiring code is.

Three properties are load-bearing and all three live here rather than in the
controller, so they cannot drift apart:

* **High entropy.** 20 characters from the 30-symbol alphabet below is ~98
  bits, drawn from :mod:`secrets`. The redemption endpoint sits at the reader
  floor, so the code is what stands between a signed-in stranger and somebody
  else's participation; it must not be guessable at any rate.
* **Hashed at rest.** Only the SHA-256 of the normalised code is stored, so a
  database read cannot redeem anything. A plain hash — not a password KDF — is
  the right tool precisely *because* the input is 100 random bits: there is no
  dictionary to stretch against, and the constant-time comparison below is done
  by the database's own index lookup on a value the attacker would already have
  to know.
* **Transcribable.** The alphabet omits ``0/O/1/I/L/U`` and the code is grouped
  in fives, because a head reads these out in a meeting. Normalisation folds
  case and drops separators, so a teacher who types the groups without dashes
  is not told their code is wrong.
"""

from __future__ import annotations

import hashlib
import re
import secrets

#: Unambiguous when read aloud or transcribed: no ``0``/``O``, ``1``/``I``/``L``
#: and no ``U`` (which is misread as ``V``). 30 symbols.
CLAIM_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
#: Characters per code and per printed group. 20 symbols is ~98 bits.
CLAIM_CODE_LENGTH = 20
CLAIM_CODE_GROUP = 5

#: Everything a transcriber may add or lose: spaces, dashes and case.
_SEPARATORS = re.compile(r"[\s-]+")


def mint_claim_code() -> str:
    """Return a fresh, grouped claim code — the only place one is generated."""
    body = "".join(
        secrets.choice(CLAIM_CODE_ALPHABET) for _ in range(CLAIM_CODE_LENGTH)
    )
    return "-".join(
        body[index : index + CLAIM_CODE_GROUP]
        for index in range(0, CLAIM_CODE_LENGTH, CLAIM_CODE_GROUP)
    )


def normalize_claim_code(code: str) -> str:
    """Fold a presented code to the form that was hashed at mint time."""
    return _SEPARATORS.sub("", code).strip().upper()


def hash_claim_code(code: str) -> str:
    """Return the stored representation of *code* (SHA-256 hex, 64 chars)."""
    return hashlib.sha256(normalize_claim_code(code).encode("utf-8")).hexdigest()


__all__ = [
    "CLAIM_CODE_ALPHABET",
    "CLAIM_CODE_GROUP",
    "CLAIM_CODE_LENGTH",
    "hash_claim_code",
    "mint_claim_code",
    "normalize_claim_code",
]
