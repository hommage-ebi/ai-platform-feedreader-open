# Third-party dependency and fixture notices

Verified against the linked upstream sources on 2026-10-19. This file is a
component inventory, not a license grant for the whole repository or a complete
license compatibility audit. Review applicable terms before redistribution or
providing a hosted service.

## Source material and test fixtures

Ordinary unit-test paper identifiers, titles, abstracts, authors and numeric
examples are synthetic parser fixtures. PDF unit tests draw original geometry
and text. The opt-in integration test uses actual Mistral 7B
(arXiv:2310.06825v1), licensed CC BY 4.0 in its official HTML.
`arxiv/tests/fixtures/mistral-7b-v1/` records attribution, license, official URLs and
hashes; its README provides explicit download/test commands. No real paper PDF,
HTML, excerpt, figure or scientific dataset is bundled as a test asset.

The runtime pipeline reads user-selected original papers and materializes only
the figures selected for an article. Papers and derived figures retain their
own rights and license terms. Public source publication does not authorize
redistribution of the user's paper or article collection.

## PyMuPDF: AGPL or separately licensed commercial use

`pymupdf==1.26.0` is used to verify PDF page bounds and render explicitly selected
page rectangles. It declares dual licensing under GNU Affero GPL 3.0 or an
Artifex commercial license. No commercial license is provided by this repository,
and this dependency is not classified as permissive. The narrow use of PDF
rendering does not itself remove applicable license obligations.

Primary sources:
- [Official PyMuPDF repository and license](https://github.com/pymupdf/PyMuPDF)
- [Official licensing information](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)

## CairoSVG and remaining dependencies

`CairoSVG==2.9.1` rasterizes selected SVG figures and declares
[LGPL-3.0-or-later](https://pypi.org/project/CairoSVG/2.9.1/).
Selected original HTML SVG bytes are preserved separately in private R2 storage
after safety validation. PDF selections are stored as raster figures.

`pyproject.toml` and `uv.lock` list the direct and transitive installed components.
Each retains its own license. The original application code is licensed under
AGPL-3.0-only; see [LICENSE](../LICENSE). Package-manager installation does
not remove third-party license obligations.

Exclude `.venv/`, caches, original papers, downloaded figures, logs, private run
records and deployment identifiers from a public source package. Runtime article
storage remains private. Containers or archives that bundle dependencies or
native libraries require a separate, complete notice and source-obligation review.
