# Signing the Windows installer with SignPath

Unsigned, the Windows installer makes Microsoft Defender SmartScreen block it ("Windows protected your PC"). [SignPath Foundation](https://signpath.org/) signs open-source projects for free, and Kato qualifies on paper: MIT licence, public repository, and releases built only by GitHub Actions. The certificate is SignPath Foundation's, so Windows shows **SignPath Foundation** as the publisher.

The release workflow already holds the signing step (`windows-sign` in `.github/workflows/desktop-release.yaml`). It stays off until the settings in step 4 exist.

## 1. Meet their conditions

From [their terms](https://signpath.org/terms):

- **Two-factor sign-in** on GitHub and on SignPath, for everyone with access.
- **A code signing policy on the website.** Add the section below to the download page (`docs/index.html`), with the real team.
- **Roles.** Authors change the code, reviewers approve changes from anyone else, and approvers approve each signing.
- **Signed releases only from this repository**, built by its GitHub Actions on GitHub's own machines. The release workflow already works this way.

## 2. Apply

Apply at [signpath.org](https://signpath.org/) with the repository (`https://github.com/shay-te/kato`) and the download page (`https://shay-te.github.io/kato/`). They review the project before accepting it.

## 3. Set up the project in SignPath

Once accepted:

1. **Connect GitHub.** Add the repository as a trusted build system (GitHub.com).
2. **Create the project.** Note its slug.
3. **Add the artifact configuration.** Paste `artifact-configuration.xml` from this folder.
4. **Set up the signing policy.** Use the release-signing policy they set up, with yourself as approver. Note its slug.
5. **Create the API token.** Make a CI user with the *submitter* role on that policy, and create its API token.

## 4. Turn it on in GitHub

In the repository's **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Variable | `SIGNPATH_ORGANIZATION_ID` | your SignPath organisation ID |
| Variable | `SIGNPATH_PROJECT_SLUG` | the project slug |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | the signing policy slug |
| Secret | `SIGNPATH_API_TOKEN` | the CI user's API token |

The next `desktop-v*` tag then builds as usual. The Windows build keeps the installer it made, and `windows-sign` sends exactly that file to SignPath, never a copy taken back out of the release. Then:

1. **It waits up to an hour** for you to approve the request in SignPath.
2. **It puts the signed installer into the draft release** in place of the unsigned one.
3. **It fixes the auto-updater's signature.** It makes the signature again over the signed file, in the `.sig` file and in `latest.json`. Without this, installed apps would refuse the update.

If another platform's build fails, the Windows installer is still signed. If the Windows build itself fails, `windows-sign` fails too, so a draft never quietly holds an unsigned installer. Re-running it signs the same built file again.

Keep one draft per tag: the signed files go back into the release named by the tag.

## The code signing policy section

Put this on the download page (`docs/index.html`), with the real team:

> **Code signing policy**
>
> Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).
>
> - **Committers and reviewers:** [Shay Tessler](https://github.com/shay-te)
> - **Approvers:** [Shay Tessler](https://github.com/shay-te)
>
> Only the Windows installer built from this repository by its GitHub Actions release workflow is signed.
>
> **Privacy:** Kato sends no data to the Kato project and collects no usage data. It connects only to the services you configure — your agent CLI and its provider (Claude Code or Codex), your issue tracker, and your git host — plus the npm registry, to tell you when your agent CLI has an update. The desktop app also checks GitHub for new releases of itself.
