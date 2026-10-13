# develop-system
matt pocock skills

Develop-system is maintained here independently of UI-Test. Source skills live in `.agents/skills/`; `release-manifest.json` enumerates the bundled engineering skills. Each skill retains its upstream license and provenance.

- [Development and verification](docs/development.md)
- [Release procedure](docs/releases.md)
- [Source import manifest](docs/provenance/import-manifest.json)
- [Issue tracker](docs/agents/issue-tracker.md)

With Python 3.11 or newer and the dependencies in `requirements-checks.txt` available:

```sh
python -B tools/checks.py
```

This tests the workflow infrastructure and bundle integrity. It does not run UI-Test business cases or migrate existing develop-system runs. Version 0.1.0 is a repository bootstrap; known F01/F02/F09 workflow fixes are separate work.
