## MyMemory 1.1.23

Snapshot from AGENT `main` at release tag **v1.1.23**.

### Release commit (local git)

**Release MyMemory 1.1.23: quote digest frontmatter scalars that contain colons.**

Quote top-level YAML values like `entity: MEDIA: platform feature` on read and rewrite so nested-mapping parses cannot break the next digest pass; keep indented participant inline maps unchanged.
