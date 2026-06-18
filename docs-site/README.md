# Kato docs site

A [Docusaurus](https://docusaurus.io) site for Kato — the marketing landing plus
the product docs (Why Kato, Getting started, Approvals, Security, Architecture).

## Run locally

```bash
cd docs-site
npm install
npm start          # dev server with hot reload at http://localhost:3000
```

## Build a static site

```bash
npm run build      # outputs to docs-site/build/
npm run serve      # preview the production build locally
```

## Where things live

- `src/pages/index.js` — the marketing landing page (hero + value props).
- `docs/*.md` — the documentation pages; order is set in `sidebars.js`.
- `docusaurus.config.js` — site title, navbar, footer, theme. Set the real
  `url` / `baseUrl` (and the `editUrl`) before deploying.
- `src/css/custom.css` — theme palette (amber on dark, matching the app).
- `static/img/logo.svg` — the logo + favicon.

## Deploy

Any static host works (the `build/` folder). For GitHub Pages, set
`organizationName` / `projectName` / `url` / `baseUrl` in `docusaurus.config.js`
and run `npm run deploy`.
