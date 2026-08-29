"""Properties of the claim-code primitive itself (remediation `W1.4`).

The route tests prove the flow; these prove the three properties the flow
rests on, at the one place they are implemented.
"""

from __future__ import annotations

from reparto_service.services.claim_codes import (
    CLAIM_CODE_ALPHABET,
    CLAIM_CODE_GROUP,
    CLAIM_CODE_LENGTH,
    hash_claim_code,
    mint_claim_code,
    normalize_claim_code,
)


def test_the_alphabet_excludes_every_character_pair_that_is_misread() -> None:
    """A head reads these out; a teacher types them back."""
    assert set("01OILU") & set(CLAIM_CODE_ALPHABET) == set()
    assert len(set(CLAIM_CODE_ALPHABET)) == len(CLAIM_CODE_ALPHABET)


def test_a_minted_code_is_grouped_and_drawn_from_the_alphabet() -> None:
    code = mint_claim_code()
    groups = code.split("-")
    assert len(groups) == CLAIM_CODE_LENGTH // CLAIM_CODE_GROUP
    assert {len(group) for group in groups} == {CLAIM_CODE_GROUP}
    assert set(code.replace("-", "")) <= set(CLAIM_CODE_ALPHABET)


def test_minting_does_not_repeat() -> None:
    """~98 bits: a collision in a hundred draws would mean the entropy is gone."""
    assert len({mint_claim_code() for _ in range(100)}) == 100


def test_normalisation_folds_what_a_transcriber_changes_and_nothing_else() -> None:
    assert normalize_claim_code(" ab-cd ef ") == "ABCDEF"
    assert normalize_claim_code("AB CD-EF") == normalize_claim_code("abcdef")
    assert normalize_claim_code("ABCDEF") != normalize_claim_code("ABCDEG")


def test_the_hash_is_of_the_normalised_code_and_is_sha256_hex() -> None:
    code = mint_claim_code()
    digest = hash_claim_code(code)
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert digest == hash_claim_code(code.lower().replace("-", ""))
    assert code.replace("-", "") not in digest
