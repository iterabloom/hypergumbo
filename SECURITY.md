# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.5.x   | :white_check_mark: |
| < 0.5   | :x:                |

Only the latest minor version receives security updates. We recommend always running the latest release.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub/Codeberg issues.**

Instead, please report them via email to:

**hypergumbo-cybersecurity@iterabloom.com**

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact
- Any suggested fixes (optional)

### What to Expect

- **Acknowledgment**: Within 72 hours of your report
- **Initial assessment**: Within 1 week
- **Resolution timeline**: Depends on severity, but we aim for:
  - Critical: 7 days
  - High: 14 days
  - Medium: 30 days
  - Low: 60 days

We will keep you informed of our progress and coordinate disclosure timing with you.

## Scope

The following are in scope for security reports:

- Remote code execution
- SQL injection, command injection, or similar injection attacks
- Authentication/authorization bypass
- Sensitive data exposure
- Denial of service (application-level)
- Supply chain attacks (malicious dependencies)

The following are generally **out of scope**:

- Vulnerabilities in dependencies (report these upstream, but let us know)
- Social engineering attacks
- Physical attacks
- Issues requiring unlikely user interaction

## Safe Harbor

We support safe harbor for security researchers who:

- Make a good faith effort to avoid privacy violations, data destruction, or service disruption
- Only interact with accounts you own or with explicit permission
- Do not exploit vulnerabilities beyond what is necessary to demonstrate the issue
- Report vulnerabilities promptly and do not publicly disclose before we've had reasonable time to address them

We will not pursue legal action against researchers who follow these guidelines.

## Security Measures

hypergumbo implements several security practices:

- **Dependency scanning**: `pip-audit` runs in CI to detect known vulnerabilities
- **Security linting**: `bandit` analyzes code for common security issues
- **Secret scanning**: `trufflehog` checks for accidentally committed secrets
- **License auditing**: Checks for problematic dependency licenses
- **100% test coverage**: Required for all code changes

## PGP Key

If you need to encrypt your report, please request our PGP key via the email above.
