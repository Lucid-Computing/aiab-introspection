from server import plan_view as pv

RAW = "bAW2VekiD4Q7Ds+znPvG65eTSJrJ3ngK3VofVki141M="
SSH = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGwFtlXpIg+EOw7Ps5z7xuuXk0iayd54Ct1aH1ZIteNT aiab-lab-key"
)


def test_base64_line_is_kept():
    assert pv.normalize_public_key(f"  {RAW}\n") == (RAW, None)


def test_ssh_line_is_unwrapped_to_the_base64_form():
    assert pv.normalize_public_key(SSH) == (RAW, None)
    assert pv.normalize_public_key("ssh-ed25519 " + SSH.split()[1]) == (RAW, None)


def test_rejects_other_text():
    for bad in ("", "hello", "ssh-rsa AAAAB3Nza", "ssh-ed25519 AAAA", RAW + " " + RAW):
        key, problem = pv.normalize_public_key(bad)
        assert key is None and problem
    assert pv.check_public_key(RAW) is None
    assert pv.check_public_key("nope")
