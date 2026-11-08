## MyMemory 1.1.37

Runtime dependencies live in one file so install, plugin load, and the cloud push cannot drift.

- `pyproject.toml` `project.dependencies` is the gateway runtime list: `onnxruntime`, `tokenizers`, `numpy`, `pyyaml`, `openai`, `ruamel.yaml`, `json5>=0.9`, and `litellm==1.108.2`.
- `dependency-groups.test` holds `pytest` and `tiktoken`. `install.sh` installs that group unless `--skip-tests` is set.
- Plugin load reads the same list with `tomllib` and still installs anything missing on a background thread. Pip index-URL lines are no longer accepted there.
- `requirements.txt` is removed. `install.sh` and the cloud push install the printed specs; they do not `pip install .`.

The pending weekly review no longer pastes the old four-part Brief or asks the model to call clarify.

- The one injection tells the model to read the YAML week file and send at most three short lines: Additions (`summary` and threads with an `outcome`), Hypotheses (open threads), and Conflicts (steps with `via: invalidates`).
- Answering that message no longer snoozes or closes the week. Close stays on `/weekly close` and the UI.
- Week generation still writes the same YAML. The no-op typed-citation pass, the legacy section parser, and the weekly HTML builder are removed.
- `install.sh` deletes a leftover `weekly_brief_validate.py` on the gateway, because its copy does not remove files that are already there.
