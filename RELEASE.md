# How to make a release

`jupyterhub-traefik-proxy` is a package available on [PyPI].

These are the instructions on how to make a release.

## Pre-requisites

- Push rights to this GitHub repository

## Steps to make a release

1. Create a PR updating `docs/source/changelog.md` with [github-activity] and
   continue when its merged. For details about this, see the [team-compass
   documentation] about it.

   [team-compass documentation]: https://jupyterhub-team-compass.readthedocs.io/en/latest/practices/releases.html

2. Checkout main and make sure it is up to date.

   ```shell
   git checkout main
   git fetch origin main
   git reset --hard origin/main
   ```

3. Update the version, make commits, and push a git tag with `tbump`.

   ```shell
   pip install tbump
   ```

   `tbump` will ask for confirmation before doing anything.

   ```shell
   # Example versions to set: 1.0.0, 1.0.0b1
   VERSION=
   tbump ${VERSION}
   ```

   Following this, the [CI system] will build and publish a release.

4. Reset the version back to dev, e.g. `1.0.1.dev` after releasing `1.0.0`.

   ```shell
   # Example version to set: 1.0.1.dev
   NEXT_VERSION=
   tbump --no-tag ${NEXT_VERSION}.dev
   ```

[github-activity]: https://github.com/executablebooks/github-activity
[pypi]: https://pypi.org/project/jupyterhub-traefik-proxy/
[ci system]: https://github.com/jupyterhub/traefik-proxy/actions/workflows/release.yaml
