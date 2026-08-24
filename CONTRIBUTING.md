# Contributing

Contributions are welcome through issues and pull requests.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make check
make test
```

Changes to the Django starter should also be applied to the bundled scaffold under
`src/django_ninja_starter/template/`. Add or update tests for behavior changes. Before
opening a pull request, run `make check`, `make test`, and `make package`.
