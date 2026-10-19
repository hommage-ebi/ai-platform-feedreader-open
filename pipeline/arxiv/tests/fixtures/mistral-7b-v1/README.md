# Real-paper integration fixture

The manifest pins actual official HTML, PDF, and six figure resources for
**Mistral 7B**, arXiv:2310.06825v1, by Albert Q. Jiang and the 17 coauthors
listed in `manifest.json`. The official HTML declares **CC BY 4.0**:
https://arxiv.org/html/2310.06825v1
https://creativecommons.org/licenses/by/4.0/

The paper and figures retain this license, independently of this repository's
license. Captions and text were reviewed for third-party credit exceptions;
none were found. This is not a license claim over referenced models/datasets.
Tests extract text and crop/rasterize figures; cached original bytes are unchanged.
No PDF or figure payload is included in Git.

From the repository root, after [shared environment setup](../../../../../docs/setup.md) succeeds:

    ARXIV_REAL_TEST=1 ARXIV_REAL_DOWNLOAD=1 pipeline/.venv/bin/python -m unittest pipeline.arxiv.tests.test_real_arxiv -v

The download flag explicitly opts into official arXiv network requests.
Subsequent runs can omit it and use only the hash-verified local cache. Override
`ARXIV_REAL_CACHE` for an existing cache. Changed bytes fail closed and require
review, not automatic hash regeneration. Ordinary test runs skip real integration.
No layout detector, OCR, checkpoint download, or inference is required.

`selections.json` contains six hand-authored deterministic selections for each
source representation: exact HTML element IDs and asset indexes, or one-based
PDF pages and explicit point-coordinate crop boxes. These are test fixtures,
not claims that a model selected figures or produced article text. PDF crops were
reviewed against the pinned source and exclude adjacent captions.

Coverage: prepare downloads only the verified original and creates no figure
inventory or crops; model task inputs contain only source and metadata; the
fixture output passes claim/finish validation without materialization; assembly
then materializes exactly the six chosen figures, verifies private in-memory
storage readbacks and decoded-pixel lossless WebP, preserves selected HTML SVG originals, and
renders six inline figures. Both real HTML and forced PDF paths are exercised,
including the SVG object and full Figure 6 screenshot. PDF selections emit only
the cropped raster, so off-crop page paths and text are never retained in an SVG. Supplemental checks cover
41 MathML nodes, five table wrappers, document-ending reference, actual PDF author
header, and nine PDF pages. Raw HTML contains malformed author/affiliation markup:
preparation deliberately marks these missing for downstream verification.

This does not certify model execution, model-generated metadata, summary quality,
translation, publication, live R2 service access, or arbitrary PDF figure selection.
