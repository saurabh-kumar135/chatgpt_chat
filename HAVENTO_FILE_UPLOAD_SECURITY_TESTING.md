# HavenTo File Upload Security Testing Report

## Scope

This document records the source-code-level security review of HavenTo's file-upload functionality. It is intended for testing against a local or staging deployment owned by the project.

## Findings

### 1. No file-type allowlist

The upload handlers derive the stored extension directly from the user's original filename:

```python
ext = os.path.splitext(photo.filename)[1] or ".jpg"
filename = f"{uuid.uuid4().hex}{ext}"
```

There is no visible allowlist restricting uploads to approved image extensions.

### 2. No MIME-type validation

The upload code does not visibly validate `UploadFile.content_type` against an approved image MIME-type list.

### 3. No magic-byte/content validation

The application reads the uploaded bytes and writes them directly to disk. There is no visible decoding step with an image library to confirm that the bytes are actually a valid image.

### 4. No upload-size validation

The handler performs:

```python
content = await photo.read()
await out_file.write(content)
```

There is no visible per-file size limit before reading the entire upload into memory.

### 5. No visible number-of-files limit

The `photos` list is processed without a visible application-level maximum number of uploaded files per request.

### 6. Potentially unsafe active-content formats

Because extensions are preserved, formats such as HTML or SVG are not visibly rejected. These formats can require special handling when served from a web origin.

### 7. Upload retrieval has no visible authorization check

The upload-serving endpoint accepts a filename and attempts to return a matching local file or GridFS object. There is no visible ownership/access-control check in the examined handler.

This may be acceptable if all home photos are intentionally public, but it should be an explicit design decision rather than an accidental consequence.

### 8. Host ownership checks need review

The examined home-edit and home-delete handlers retrieve a home by ID but do not visibly verify that the authenticated user owns that home before modifying or deleting it. This is a separate broken-access-control issue that can interact with photo replacement.

## Safe test cases

Run these only against a local or staging deployment that you own.

| Test | Expected secure behavior |
|---|---|
| Valid PNG/JPEG/WebP | Accepted |
| Plain text renamed to `.jpg` | Rejected |
| HTML renamed to `.jpg` | Rejected |
| SVG containing script | Rejected unless SVG is deliberately supported and safely sanitized |
| Oversized image | Rejected before excessive memory/disk use |
| Excessive number of photos | Rejected after configured maximum |
| Filename containing `../` | Stored using generated server-side name; never escapes upload directory |
| Special/unusual filename | Server-generated filename used safely |
| User B requests User A's private upload | Denied if uploads are private |
| User B edits User A's home ID | Denied with authorization failure |
| User B deletes User A's home ID | Denied with authorization failure |

## Recommended remediation

1. Allow only explicitly supported image formats, preferably JPEG, PNG, and WebP.
2. Validate both the declared MIME type and the actual file signature/content.
3. Decode images with a trusted image library such as Pillow and reject files that cannot be decoded.
4. Enforce a strict per-file upload-size limit before loading the complete file into memory.
5. Enforce a maximum number of files per request.
6. Reject HTML and SVG unless they are specifically required and safely sanitized.
7. Continue using unpredictable server-generated filenames rather than user-controlled names.
8. Store uploaded media outside executable/application-code directories and configure media responses with safe content types.
9. Add `X-Content-Type-Options: nosniff` to media responses where appropriate.
10. Decide explicitly whether home photos are public. If private, enforce authentication and ownership/authorization checks on retrieval.
11. Before editing or deleting a home, verify `home.host == user.id` (using the project's actual ID representation).
12. Delete or replace obsolete uploaded files to avoid unbounded orphaned storage.
13. Add automated regression tests for all of the cases above.

## Important limitation

This report is a source-code review, not a live exploit confirmation. Dynamic testing requires a running local/staging HavenTo instance. A finding such as missing validation is confirmed in code, while the exact runtime impact should be verified in the controlled deployment.
