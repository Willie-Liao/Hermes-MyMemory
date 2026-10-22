## MyMemory 1.1.30

### Monthly comparison

- `comparison_with_last_month` is `unchanged` and `changed` rows (text plus card ids) plus one `suggestion`. The old free-text `summary` paragraph is no longer written.
- An old month file still loads. A leftover `summary` key is ignored, and the installer drops that key from month files already in the Hermes home.
- The monthly suggest reply prints one plain line per item, so markdown inside a body cannot open a new heading.

### Package metadata

- `pyproject.toml` names the plugin `hermes-plugin-mymemory` at 1.1.30, depends on PyYAML, and builds a hatchling wheel of the `MyMemory` package.
