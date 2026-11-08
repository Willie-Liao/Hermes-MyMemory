## MyMemory 1.1.37

Runtime dependencies live in one file so install, plugin load, and the cloud push cannot drift.

- `pyproject.toml` `project.dependencies` is the gateway runtime list: `onnxruntime`, `tokenizers`, `numpy`, `pyyaml`, `openai`, `ruamel.yaml`, `json5>=0.9`, and `litellm==1.108.2`.
- `dependency-groups.test` holds `pytest` and `tiktoken`. `install.sh` installs that group unless `--skip-tests` is set.
- Plugin load reads the same list with `tomllib` and still installs anything missing on a background thread. Pip index-URL lines are no longer accepted there.
- `requirements.txt` is removed. `install.sh` and the cloud push install the printed specs; they do not `pip install .`.
