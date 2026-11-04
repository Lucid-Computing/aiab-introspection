# Tinfoil deployment

The box runs as one container in a Tinfoil confidential VM. Everything about releasing
it, the release pins and the verification steps is in [audit/README.md](audit/README.md);
the release procedure itself is [audit/release.py](audit/release.py), driven by the
`release.yml` workflow. `workflows/` holds the two workflow files the config repository
carries.
