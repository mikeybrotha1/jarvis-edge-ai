# GitHub Pages showcase

The portfolio page lives in [`/site`](../site). It is static HTML, CSS, and a
small script. There is no build step and no framework.

[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) publishes that
folder with GitHub Actions. This repository does not turn Pages on by itself.

## Enable Pages

1. Merge the workflow to the default branch (`main`).
2. On GitHub, open **Settings**.
3. Open **Pages**.
4. Under **Build and deployment**, set **Source** to **GitHub Actions**.
5. Push to `main` (or run the workflow manually). The action uploads `/site`
   and deploys it.
6. The site URL is <https://mikeybrotha1.github.io/jarvis-edge-ai/>.

The first deployment can take a minute after Pages is switched to GitHub
Actions. Later pushes that change `site/` or the workflow publish again.

## Local preview

From the repository root:

```bash
python3 -m http.server 8765 --directory site
```

Open <http://127.0.0.1:8765/>. Asset paths are relative, so the same files work
at the project-site URL above.
