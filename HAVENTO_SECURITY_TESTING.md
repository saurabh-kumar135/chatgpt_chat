# HavenTo Security Testing Plan

## Important distinction

**CVE scanning is not the same as complete website security testing.**

A CVE scanner primarily finds known vulnerabilities in third-party dependencies, base images, and other software components. It will not reliably detect application-level problems such as IDOR/Broken Access Control, business-logic flaws, insecure authorization, XSS in your own code, or unsafe file handling.

For HavenTo, use several layers of testing.

## 1. Python dependency CVEs

From the backend directory:

```bash
cd backend
python -m pip install pip-audit
pip-audit -r requirements.txt
```

Also audit the actual installed environment when possible, because `requirements.txt` contains version ranges and transitive dependencies matter.

Pay particular attention to FastAPI, Starlette, Pydantic, Uvicorn, PyMongo/Motor/Beanie, requests/httpx, authentication libraries, multipart handling, and any other internet-facing dependency.

## 2. Docker/base-image CVEs

Build the backend image and scan it with Trivy:

```bash
docker build -t havento-backend ./backend
trivy image havento-backend
trivy image --severity HIGH,CRITICAL havento-backend
```

This checks OS packages and packages installed inside the image in addition to application dependencies that Trivy can identify.

## 3. Frontend dependency CVEs

From the frontend/client directory:

```bash
cd client
npm audit
npm audit --audit-level=high
```

Review the lockfile as well as `package.json`. Do not blindly run `npm audit fix --force` because it can introduce breaking dependency upgrades.

## 4. Python static security analysis

Use Bandit:

```bash
python -m pip install bandit
bandit -r backend
```

For a machine-readable report:

```bash
bandit -r backend -f json -o bandit-report.json
```

## 5. SAST with Semgrep

```bash
python -m pip install semgrep
semgrep scan --config p/python backend
```

Use this to find common insecure coding patterns that dependency scanners cannot see.

## 6. Secret scanning

Because the repository is public, scan both the working tree and Git history for accidentally committed credentials:

```bash
gitleaks detect --source . --verbose
```

Also check GitHub's secret-scanning/Dependabot features.

**Important:** if a real secret was ever committed, deleting it from the latest commit is not enough. Rotate/revoke the secret because Git history may still contain it.

## 7. Dynamic web/API testing with OWASP ZAP

Run HavenTo locally or against a dedicated staging deployment rather than attacking production.

Use OWASP ZAP to:

1. Crawl/spider the application.
2. Run passive scanning.
3. Review discovered endpoints and parameters.
4. Run an active scan against your own staging instance.
5. Review alerts manually; scanners can produce false positives and can miss authorization/business-logic flaws.

## 8. Broken Access Control / IDOR

This is one of the highest-priority areas for a booking platform.

Create at least two test accounts, for example User A and User B.

Test whether User A can access or modify resources belonging to User B by changing identifiers such as:

- user IDs
- home/property IDs
- booking IDs
- host IDs
- agent IDs
- analytics identifiers
- upload/file identifiers

For every endpoint, authorization must be enforced server-side. Hiding a button in the frontend is not security.

Examples of questions to test:

- Can User A read User B's booking?
- Can User A cancel User B's booking?
- Can a normal user call a host-only endpoint directly?
- Can Host A modify Host B's property?
- Can a user access another user's uploaded file?
- Can an agent access data outside its authorized scope?

## 9. Authentication and session/token security

Test:

- invalid credentials
- repeated failed logins and rate limiting
- token expiration
- malformed/expired JWTs
- authorization after logout/expiration
- password-change invalidation behavior
- password-reset token expiration and single-use behavior
- email-verification token expiration and single-use behavior
- privilege escalation from a normal user to host/agent/admin

Do not test destructive account actions against real user data; use test accounts.

## 10. MongoDB / NoSQL injection

You said you previously found no SQL injection. HavenTo uses MongoDB, so **NoSQL/MongoDB operator injection** is the more relevant injection class.

Review every place where request-controlled data reaches MongoDB filters, updates, sorting, projection, or aggregation.

The important question is not simply whether a string such as `' OR 1=1` works. Instead, verify that untrusted JSON/object input cannot become MongoDB operators such as `$ne`, `$gt`, `$in`, `$regex`, `$where`, or unexpected nested query structures.

Perform these tests only against your local/staging application and with harmless test data.

## 11. XSS

Test user-controlled fields such as:

- property/home title
- description
- location
- reviews
- usernames/names
- messages
- other text fields rendered into HTML

Check for stored, reflected, and DOM-based XSS.

The key question is whether user-controlled data is safely encoded before being inserted into HTML, JavaScript, URLs, or other interpreters.

## 12. File-upload security

HavenTo has upload and GridFS-related functionality, so test this carefully.

Check:

- allowed extensions
- MIME-type validation
- file signature/magic-byte validation
- maximum file size
- filename handling
- path traversal
- executable/script uploads
- SVG/HTML content handling
- authorization when retrieving files
- whether one user can access another user's file
- GridFS object/file-name access controls
- storage outside directly executable web directories

Your use of `os.path.basename()` in the upload route is useful against basic path traversal, but it does not by itself prove that upload retrieval is authorized or safe.

## 13. Error handling / information disclosure

Review global exception handling carefully.

If the application returns raw exception text to clients, internal implementation details can leak. Production responses should normally return a generic error message while detailed exceptions go only to protected server logs.

Avoid returning:

- database errors
- filesystem paths
- stack traces
- internal object details
- credentials/tokens
- framework/debug information

## 14. CORS and security headers

Check the deployed response headers:

```bash
curl -I https://YOUR-HAVENTO-DOMAIN
```

Review at least:

- Content-Security-Policy (CSP)
- Strict-Transport-Security (HSTS)
- X-Content-Type-Options
- Referrer-Policy
- Permissions-Policy
- appropriate Cache-Control behavior for sensitive responses

Also review CORS carefully. Do not allow arbitrary origins together with credentials unless that behavior is explicitly intended and safe.

## 15. Business-logic security

Automated CVE scanners will not reliably find these.

For HavenTo, test cases such as:

- changing the price in the client request before booking
- modifying another user's property
- modifying another host's booking
- cancelling a booking without authorization
- booking unavailable dates
- bypassing booking-state transitions
- manipulating quantities/dates/prices
- accessing analytics belonging to another account
- performing host/agent operations as a normal user
- replaying requests that should only work once

Never trust prices, roles, ownership, or booking state supplied only by the frontend.

## 16. Recommended security-testing stack for HavenTo

Use the following combination:

| Layer | Tool | Purpose |
|---|---|---|
| Python dependencies | pip-audit | Known Python CVEs |
| Docker | Trivy | OS/base-image/container vulnerabilities |
| Frontend | npm audit | Node dependency vulnerabilities |
| Python SAST | Bandit | Python insecure coding patterns |
| SAST | Semgrep | Broader source-code security patterns |
| Secrets | Gitleaks + GitHub scanning | Credential leakage |
| DAST | OWASP ZAP | Runtime web/API vulnerabilities |
| Manual testing | Two test accounts | IDOR/access control/business logic |
| Database review | Code + safe NoSQL tests | MongoDB injection |

## 17. What “secure against all CVEs” should mean

There is no practical command that proves a website is secure against **all CVEs**.

A better security goal is:

1. Identify every dependency and base image.
2. Scan them against current vulnerability databases.
3. Pin and regularly update dependencies.
4. Run automated security checks in CI/CD.
5. Perform DAST against staging.
6. Manually test authorization, authentication, uploads, injection, XSS, and business logic.
7. Re-test after fixes.

## 18. Recommended CI security pipeline

For HavenTo, add security checks to GitHub Actions so they run automatically on pull requests and important pushes.

A useful pipeline should include:

```text
Pull Request
    |
    +--> pip-audit
    +--> npm audit
    +--> Bandit
    +--> Semgrep
    +--> Trivy
    +--> Secret scanning
    |
    +--> Tests
    |
    +--> Deploy to staging
             |
             +--> OWASP ZAP
```

## Priority order for HavenTo

Do these first:

1. **Broken access control / IDOR**
2. **Authentication and password-reset security**
3. **File upload and GridFS access control**
4. **MongoDB/NoSQL injection review**
5. **XSS**
6. **Secrets scanning**
7. **Dependency CVE scanning**
8. **Docker image scanning**
9. **Security headers/CORS**
10. **Business-logic testing**

The most important point is that finding “no SQL injection” does not mean HavenTo is secure. Because HavenTo uses MongoDB, access control, NoSQL injection, authentication, file handling, and business logic deserve particular attention.
