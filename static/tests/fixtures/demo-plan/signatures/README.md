# signatures/

Each party signs the plan digest here, offline, with its own Ed25519 key:

- `lab.sig`
- `auditor.sig`

The plan digest is the hash of `plan/` without `tests/` and `signatures/`, so these files never change the digest they sign. They are produced by the `aiab plan sign` command (static part, to come) and verified by CI before merge and by the box at boot. Private keys never enter the repository.
