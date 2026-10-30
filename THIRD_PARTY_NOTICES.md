# Third-party components and public-source preparation

The original application code is licensed under AGPL-3.0-only; see [LICENSE](LICENSE). Third-party components retain their respective licenses. This file records those components and their notices.

## Vendored Sites build adapter

`build/sites-vite-plugin.ts` is derived from `@openai/sites-vite-plugin` 0.2.0. It is marked Copyright (c) 2026 OpenAI and MIT-licensed. The complete notice is preserved in `build/sites-vite-plugin.LICENSE`. The local adaptation resolves deployment metadata from the repository root. No authentication token is bundled.

## PDF rendering and Python pipeline

See [pipeline/THIRD_PARTY_NOTICES.md](pipeline/THIRD_PARTY_NOTICES.md) for the pipeline's source-material and dependency notices. PyMuPDF renders selected PDF figure regions and is available under AGPL or a separately obtained Artifex commercial license. This repository supplies no commercial license. Review the applicable obligations before distributing or providing a hosted combined application; do not describe the whole project or all dependencies as permissively licensed.

The portable code contains no real-paper PDFs, figures, article text or private Site account identifiers. Unit examples use fictional article records and original PDF geometry. The opt-in Mistral 7B integration test retains official source URLs, attribution, CC BY 4.0 evidence and SHA-256 hashes in `pipeline/arxiv/tests/fixtures/mistral-7b-v1/`, without bundling source payloads. Before adding another real-paper example, retain its verified permissive license and attribution; a paper being accessible on arXiv is not enough.

## Browser distribution notices

The build emits `dist/client/THIRD_PARTY_LICENSES.txt`, linked from the Reader,
with the installed KaTeX version's full MIT copyright/permission notice and the
installed DOMPurify version's copyright attribution and supplied Apache-2.0 and
MPL-2.0 license texts. DOMPurify declares `(MPL-2.0 OR Apache-2.0)`; providing both
texts preserves that upstream choice. These notices cover browser bundles and
KaTeX font assets and do not assign a license to the original application code.
`verify-content.mjs` rejects missing or altered copies before release.

Primary sources:
- [KaTeX license](https://github.com/KaTeX/KaTeX/blob/main/LICENSE)
- [DOMPurify licenses](https://github.com/cure53/DOMPurify)

## Source icons

The sidebar uses the [arXiv favicon](https://arxiv.org/static/browse/0.3.4/images/icons/favicon-32x32.png) supplied by arXiv. This mark identifies the source of articles and remains the property of its owner; it is not licensed under this project's code license.

## Other dependencies

Node and Python dependencies are pinned in `package-lock.json` and `pipeline/uv.lock`; dependency code is not included in the source archive. Each package retains its own upstream license. Installing or distributing dependencies is separate from choosing a license for this repository.

The Python pipeline also directly uses `CairoSVG==2.9.1`, which declares
[LGPL-3.0-or-later](https://pypi.org/project/CairoSVG/2.9.1/). The Node build
toolchain includes MPL-2.0 Lightning CSS and LGPL-3.0-or-later libvips platform
packages used by sharp. These build-tool licenses do not by themselves license
the generated application. Containers or archives that bundle dependencies or
native libraries need a separate, complete notice and source-obligation review;
this inventory is not a full software bill of materials.

The repository's hosting profile is a template. Configure the target project ID privately; public sharing or deployment is never implied by building this code.
