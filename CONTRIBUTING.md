# Contributing to Lancy

Contributions are welcome — retrieval strategies, chunkers, frontend improvements, documentation, bug fixes.

---

## Reporting bugs and requesting features

Use GitHub Issues:

- **Bug report** — describe what happened, what you expected, and how to reproduce it. Include your OS, Python version, and relevant log output from `logs/backend.log`.
- **Feature request** — describe the problem you're trying to solve and your proposed solution. If you have an alternative in mind, mention it.

Check existing issues before opening a new one.

---

## Contributing code

1. Fork the repository and create a branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. Make your changes. Follow the conventions in [CLAUDE.md](CLAUDE.md):
   - Conventional Commits (`feat`, `fix`, `refactor`, `docs`, `chore`)
   - Surgical changes — touch only what you must


3. Test your changes against the demo dataset (PrimePack AG in `data/`).

4. Open a pull request against `main`. Describe what changed and why — link the related issue if one exists.

---

## What to work on

See [BACKLOG.md](BACKLOG.md) for open tasks and research directions. Items marked as research are exploratory — feel free to propose a direction before implementing.

---

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE). All copyright notices must be preserved as required by the license.
