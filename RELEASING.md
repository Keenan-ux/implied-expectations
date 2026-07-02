# Releasing to PyPI

One-time setup (requires the PyPI account owner):

1. Create or sign in to a PyPI account at https://pypi.org.
2. Go to https://pypi.org/manage/account/publishing/ and add a
   **pending publisher** with exactly these values:
   - PyPI project name: `implied-expectations`
   - Owner: `Keenan-ux`
   - Repository name: `implied-expectations`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In this GitHub repo: Settings -> Environments -> New environment -> `pypi`.

Per release:

1. Bump `version` in `pyproject.toml` and in `src/implied_expectations/__init__.py`.
2. Commit, push, wait for CI to pass.
3. Create a GitHub release with a `vX.Y.Z` tag:
   `gh release create v0.1.0 --title v0.1.0 --notes "..."`.
4. The `publish` workflow builds, checks, and uploads via trusted publishing.
   No API tokens are stored in the repo or in Actions secrets.
