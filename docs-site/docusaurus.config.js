// @ts-check
// Docusaurus v3 config. Run `npm install` then `npm start` in docs-site/.
const { themes } = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Kato',
  tagline: 'The autonomous coding agent you can actually govern.',
  favicon: 'img/kato.png',

  // Update these to your real domain when you deploy.
  url: 'https://your-kato-docs.example.com',
  baseUrl: '/',

  organizationName: 'kato',
  projectName: 'kato',

  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',

  i18n: { defaultLocale: 'en', locales: ['en'] },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve('./sidebars.js'),
          // Set this to your repo to show an "Edit this page" link.
          // editUrl: 'https://github.com/your-org/kato/tree/master/docs-site/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/kato.png',
      colorMode: { defaultMode: 'dark', respectPrefersColorScheme: true },
      navbar: {
        title: 'Kato',
        logo: { alt: 'Kato', src: 'img/kato.png' },
        items: [
          { type: 'docSidebar', sidebarId: 'docs', position: 'left', label: 'Docs' },
          { to: '/docs/getting-started', label: 'Get started', position: 'left' },
          { to: '/docs/security', label: 'Security', position: 'left' },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Product',
            items: [
              { label: 'Why Kato', to: '/docs/why-kato' },
              { label: 'Get started', to: '/docs/getting-started' },
              { label: 'Security', to: '/docs/security' },
            ],
          },
          {
            title: 'Learn',
            items: [
              { label: 'Approvals', to: '/docs/approvals' },
              { label: 'Architecture', to: '/docs/architecture' },
            ],
          },
        ],
        copyright: `Kato — built ${new Date().getFullYear()}. Ship more tickets, stay in control.`,
      },
      prism: {
        theme: themes.github,
        darkTheme: themes.dracula,
      },
    }),
};

module.exports = config;
