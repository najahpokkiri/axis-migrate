# F11 · Target runtime I/O (Databricks targets only)

Open when: the target is Databricks (or any cloud mount) and you write or review `databricks/` files or I/O code that
will run there. It serves the target checks (`target_check.py`), not a parity eval. Going through ctx already gives
you X-1 and X-2: the wheel entry point builds the same Ctx from task parameters.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **X-1** · FUSE and object-store mounts cannot seek: GTiff, GPKG and XLSX are written to local scratch and copied, and GPKG, SQLite and XLSX reads are staged locally; ctx does both for `no_seek_roots`, so never bypass `ctx.output_path` · fails if ignored: corrupt or failed writes on Volumes · evidence: ev:748d718a42, ev:3d92f9c1d9, ev:9a256112b6, ev:b25a18c5ac, ev:4a3ce3b1b6, ev:b98d55743e · eval: target · form: R
- **X-2** · `__file__` is undefined and workspace files become notebooks: every path comes from task parameters (input, work and output roots) · fails if ignored: NameError or path-not-found on the cluster · evidence: ev:4616d92cb1, ev:1f543bd5c7, ev:59f609976d · eval: target · form: S+R (S11)
- **X-3** · The job runs the verified code: the wheel is built from the sealed code tree and the code hash is logged at task start · fails if ignored: deployed code differs from what was verified · evidence: ev:d873fb0762, ev:4616d92cb1 · eval: target · form: R
- **X-4** · One lockfile for sandbox and target; never upgrade core packages inside a managed runtime and never add blanket pins on old major versions by habit · fails if ignored: version drift; ABI crashes at import · evidence: ev:99a898fc9b, ev:b5380e1d91, ev:a1df223e06 · eval: target · form: P
- **X-5** · Persist geometry as WKB or GeoParquet, not WKT · fails if ignored: precision loss; bloated tables · evidence: ev:ffde25edb0 · eval: target · form: P
- **X-6** · Cloud-optimised GeoTIFFs via `driver="COG"`; VRT source paths valid where the job runs · fails if ignored: files that are not COGs; broken VRTs · evidence: ev:8742679414, ev:7b8414541d · eval: target · form: P
- **X-7** · Staging directories come from `tempfile.mkdtemp()` (or the task's scratch root), never a hard-coded local-disk path; checkpoints that must survive a restart do not live on ephemeral disk · fails if ignored: writes to a path that does not exist on the runtime · evidence: ev:3d92f9c1d9, ev:9a256112b6 · eval: target · form: P
