# Repository Architecture

`blue-llmsource` is the engineering repository. It should contain reusable
source code, tests, configuration, and engineering documentation.

The full historical research working tree—including datasets, generated
artifacts, ignored files, and source-untracked files at migration time—belongs
in the separate `pkdb` repository. Only the paths tracked by the source
repository at migration time were included in the fresh target commit; the
remaining copied content is intentionally local until it receives a separate
publication and secret-review decision.
