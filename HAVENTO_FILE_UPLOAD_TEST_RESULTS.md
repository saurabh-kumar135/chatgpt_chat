# HavenTo File Upload Security Test Results

## Scope

Source-code-level security testing of the current `main` branch of `saurabh-kumar135/havento-accomodation-booking-platform`.

These results are not live HTTP exploit confirmations because a running local/staging deployment was not available in this review.

## Results

| # | Test case | Result | Severity | Finding |
|---|---|---|---|---|
| 1 | Valid PNG/JPEG/WebP upload | PARTIAL | Medium | Upload is accepted, but there is no visible MIME, magic-byte, or image-content validation. |
| 2 | Plain text renamed `.jpg` | FAIL | High | Uploaded bytes are written directly without confirming they are an image. |
| 3 | HTML renamed `.jpg` | FAIL | High | No content validation or strict extension allowlist is visible. |
| 4 | SVG containing JavaScript | FAIL | High | SVG is not visibly rejected and the supplied extension is preserved. |
| 5 | Oversized file | FAIL | High | The complete file is read with `await photo.read()` and no application-level size limit is visible. |
| 6 | Excessive number of photos | FAIL | Medium/High | No visible maximum number of files per request. |
| 7 | `../` filename/path traversal | PASS | — | Upload storage uses a UUID-generated filename rather than the original path/name. |
| 8 | Special/unusual filename | PASS | — | The stored filename is generated server-side with `uuid.uuid4().hex`. |
| 9 | User B accesses User A's upload | POTENTIAL FAIL | High | Upload-serving routes have no visible authentication or ownership check. This is only a vulnerability if the media is intended to be private. |
| 10 | User B edits/deletes User A's home | FAIL | Critical | Edit/delete handlers retrieve a home by ID but do not visibly verify that `home.host` belongs to the authenticated user. |

## Confirmed source-level issues

1. Missing upload type allowlist.
2. Missing MIME validation.
3. Missing magic-byte/image-content validation.
4. Missing upload-size limit.
5. Missing maximum-file-count enforcement.
6. Potentially unsafe active-content formats such as HTML/SVG are not visibly blocked.
7. Upload retrieval lacks visible authorization checks.
8. Home edit/delete lack visible host-ownership checks.

## Positive controls observed

- Uploaded filenames are replaced with unpredictable UUID-based names.
- The original filename is therefore not directly used as the disk filename.
- Basic path traversal through the uploaded filename is consequently mitigated.

## Priority

### Critical

- Add an ownership check before home edit and delete operations.

### High

- Allowlist image formats.
- Validate MIME type and actual file signature/content.
- Decode uploaded images with a trusted image library.
- Enforce strict per-file size limits.
- Reject HTML/SVG unless deliberately supported and safely sanitized.
- Decide whether media is public or private and enforce authorization accordingly.

### Medium

- Limit the number of uploaded files per request.
- Add automated regression tests for all upload cases.
- Remove orphaned files when homes/photos are replaced or deleted.

## Important limitation

A source-level PASS/FAIL does not equal a live exploit result. Dynamic testing should be performed against a local or staging deployment owned by the project, using a dedicated test account.
