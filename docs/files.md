# Files

Owned file uploads over REST, GraphQL and gRPC. Optional: naming it in
`DJANGO_FILES_ENABLED` installs its table, routes and admin; a project that does
not name it never imports the package. It lives in `src/apps/files`.

- **Storage** is Django's storage API. `DJANGO_STORAGE_BACKEND=filesystem` (the
  default) writes under `MEDIA_ROOT`; `DJANGO_STORAGE_BACKEND=s3` writes to a
  private bucket through django-storages (`pip install '.[s3]'`), configured by
  `DJANGO_AWS_STORAGE_BUCKET_NAME`, `DJANGO_AWS_S3_REGION_NAME`,
  `DJANGO_AWS_S3_ENDPOINT_URL`, `DJANGO_AWS_ACCESS_KEY_ID` and
  `DJANGO_AWS_SECRET_ACCESS_KEY`.
- **Validation**: a size limit (`DJANGO_FILES_MAX_UPLOAD_MB`) checked before the
  content is read, then the content type sniffed from the first bytes. The
  extension must be one the sniffed type is known by, so a renamed file is
  refused. Accepted: PNG, JPEG, GIF, WebP, PDF, zip (and Office documents),
  gzip, and plain text (`.txt .csv .json .md .log`). Nothing a browser executes.
- **Names**: stored under `<DJANGO_FILES_UPLOAD_PATH>/YYYY/MM/DD/<random>.<ext>`;
  the uploader's name is kept on the row and used only as the download filename.
- **Access**: the owner and staff may list, read, download and delete. Anybody
  else gets 404, the same as a missing id. Downloads are always attachments.
- Uploads share the `upload` rate-limit scope (`DJANGO_API_THROTTLE_UPLOAD`).

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/files` | Bearer | List files this account may see |
| `POST` | `/api/v1/files` | Bearer | Upload a file |
| `DELETE` | `/api/v1/files/{file_id}` | Bearer | Delete one file |
| `GET` | `/api/v1/files/{file_id}` | Bearer | Read one file's metadata |
| `GET` | `/api/v1/files/{file_id}/download` | Bearer | Download one file's bytes |
<!-- /generated:routes -->

GraphQL: `files`, `file`, `fileContent` (base64), `uploadFile(name, contentBase64)`,
`deleteFile`. gRPC: `FileController` with `List`, `Get`, `Upload`, `Download`,
`Delete`, the bytes as a protobuf `bytes` field.

## Models

<!-- generated:models -->
#### `StoredFile`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `owner` | ForeignKey | → `accounts.User` |
| `name` | Char |  |
| `storage_key` | Char | unique, not editable |
| `content_type` | Char |  |
| `size` | PositiveBigInteger |  |
| `created_at` | DateTime | not editable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `StoredFile` | Yes | — | `name`, `owner`, `content_type`, `size`, `created_at` |
<!-- /generated:admin -->

## Settings

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_FILES_ENABLED` | **Yes** | whether this deployment carries file uploads at all. |
| `DJANGO_FILES_UPLOAD_PATH` | **Yes** | the folder or key prefix inside the configured storage files go under. |
| `DJANGO_FILES_MAX_UPLOAD_MB` | Optional | the largest file anybody may upload, in megabytes; zero means no limit. 0 or more. |
<!-- /generated:settings -->
