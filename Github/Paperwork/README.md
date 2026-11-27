# PDF Workplace

PDF Workplace is an independently written PDF utility app. It has a browser frontend, a FastAPI backend, PostgreSQL-backed user and job records, Microsoft Entra ID sign-in (OIDC), organization user administration, and a Docker Compose deployment. Its header uses the supplied Info Origin logo.

The app implements PDF merge, split, crop, rotate, reorder, extraction and removal of pages, compression, watermarks, redaction, annotation and image removal, form editing and detection, text and image placement, color adjustments, repair, overlays, broad file conversion, password protection and removal, and personal or certificate-based signing. Direct tool uploads are processed in memory and are not retained. PDFs explicitly added to a server folder are encrypted and persisted in PostgreSQL. Successful operation metadata is also recorded there.

## Run locally with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set `SESSION_SECRET` to a random value of at least 32 characters.
3. Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and set it as `STORAGE_ENCRYPTION_KEY`. Keep this key secret and back it up separately; encrypted PDFs and saved signatures cannot be recovered if it is lost.
4. Configure `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, and `ENTRA_CLIENT_SECRET`. Set `INITIAL_ADMIN_EMAILS` to one or more trusted Entra sign-in email/username values for organization owners. Matching existing accounts are promoted on startup; new accounts become owners when they sign in.
5. Configure the single local administrator with `LOCAL_ADMIN_USERNAME`, `LOCAL_ADMIN_EMAIL`, and a strong `LOCAL_ADMIN_PASSWORD` of at least 12 characters. The password is scrypt-hashed in memory for verification and is not stored in PostgreSQL. The local email identifies the existing owner account. While local credentials are enabled, that account cannot sign in through Entra.
6. In the Entra app registration, add a **Web** redirect URI matching `PUBLIC_BASE_URL` plus `/auth/callback` (for the defaults: `http://localhost:8000/auth/callback`). Add the OpenID Connect `openid`, `profile`, and `email` delegated scopes.
7. In the corresponding Entra Enterprise Application, set **Assignment required?** to **Yes**, then assign only the users or groups allowed to use PDF Workplace. Entra will block sign-in for users without application access.
8. Run `docker compose up --build`.
9. Open `http://localhost:8000`.

The local PostgreSQL password in the Compose defaults is for local evaluation only. Set a strong `POSTGRES_PASSWORD` in `.env` for any shared deployment. Compose builds the database URL from that value unless `DATABASE_URL` is explicitly set; if you set both, ensure their credentials match. For production, terminate TLS at a trusted reverse proxy, set `PUBLIC_BASE_URL` to the public HTTPS URL and `COOKIE_SECURE=true`, and use a managed/secured PostgreSQL deployment and secret store. Do not commit `.env`.

The app can start without Entra credentials, but the Entra button is hidden until they are supplied. Local administrator sign-in remains unavailable until its username, email, and password are configured. Encrypted folder creation is unavailable until `STORAGE_ENCRYPTION_KEY` is configured. Accounts are provisioned on first successful SSO sign-in; there is no application user or seat cap. Organization owners and admins can search and page through users, create teams, assign users to teams, set roles and Editor/Processor capabilities, and deactivate or reactivate accounts. Admins can create pending app invitations with a team, role, and capabilities; no email is sent, and invited addresses are applied on their first Entra sign-in. Invitees must also be assigned to the Entra Enterprise Application. The account menu links to theme settings, user management, read-only server settings, and usage metrics; billing and seat limits are not configured.

Application roles are **Org Owner**, **Admin**, **Team Lead**, and **Member**. Only owners and admins can access the administration page; Team Lead currently has no team-scoped management powers. The environment-configured owner cannot be changed in the app. At least one active organization owner must remain. Deactivated users cannot use the app even if they can still authenticate with Entra. Existing databases are upgraded to add team, capability, role, status, and last-sign-in fields at startup.

Editor capability allows page editing, annotation, form, color, and signature tools. Processor capability allows merge, split, conversion, compression, and document-security tools. Both capabilities are enforced in the interface and by the API; existing users retain both by default. Server settings never display secrets, and Usage reports processing and stored-file metrics without billing or seat quotas.

## Operations

After a file operation finishes, PDF Workplace stages the output in a review panel instead of downloading it automatically. PDFs show a first-page preview when renderable, individual image outputs show an image preview, and archive outputs show file details. Select **Download** to save the result or **Undo** to discard it. Original files are not modified, and no output download begins until the user chooses **Download**.

The default Docker image includes LibreOffice, Calibre, and RAR archive support for file conversion, so its first build is substantially larger and slower than the PDF-only image. The build still omits the 62 MB OpenCV wheel to reduce download time. Automatic form detection and scanned-photo splitting are hidden until the optional vision dependency is installed. To include those tools, build with `docker compose build --build-arg INSTALL_VISION=true app`, then start with `docker compose up -d`.

- **Merge:** upload two or more PDFs.
- **Split:** choose page ranges, chapters, sections, file-size limits (0.1-100 MB), page-count groups, document-count groups, page-divider text, or printable chunks. Page ranges use semicolons, e.g. `1-3;4-6`. Chapter splitting uses level-1 bookmarks or headings beginning with "Chapter"; section splitting uses level-2 bookmarks or numbered section headings. Divider pages containing the supplied text are removed. Printable chunks tile oversized pages into A4-sized pieces.
- **Crop:** select a rectangle on the first-page preview; the same proportional crop is applied to every page. Crop preview requires one temporary PDF.
- **Rotate:** rotate all pages by 90, 180, or 270 degrees clockwise.
- **Reorder:** enter every page number exactly once, e.g. `3,1,2`.
- **Extract:** enter the pages or ranges to keep, e.g. `1-3,5`.
- **Compress:** rewrite the PDF with compressed page content streams and deduplicated objects. This may not reduce every PDF.
- **Add Watermark:** apply centered text on each page.
- **Add Stamp to PDF:** add text, a PNG/JPEG image, or both to every page; choose a corner or center position.
- **Sanitize:** remove document metadata, XML metadata, embedded files, links, JavaScript, annotations, and form responses. Review the result because this intentionally removes document information.
- **Flatten:** bake form-field and annotation appearances into the page while keeping the page's text content. This changes the PDF and invalidates any existing digital signatures.
- **Redact:** remove matching text from page content and cover its matching areas. Review the output before sharing; OCR text in images and other visual representations may require separate handling.
- **Images to PDF:** combine PNG or JPEG files into a PDF.
- **PDF to images:** render each page as a 150 DPI PNG in a ZIP file. Conversion is limited to 50 million output pixels per job.
- **Convert files:** choose a source format and a compatible target format. Word documents (DOCX, DOC, ODT), spreadsheets (XLSX, XLS, ODS), and presentations (PPTX, PPT, ODP) convert within their document family or to PDF using LibreOffice. PNG, JPEG, GIF, BMP, TIFF, and WebP convert to PDF or another raster image format; SVG converts to PDF or supported raster formats. PDFs convert to raster images or CBZ; CBZ and CBR comics convert to PDF, and CBR can also convert to CBZ. EML and MSG emails convert to PDF (message text and headers only; attachments are not included). EPUB, MOBI, AZW3, and FB2 ebooks convert to PDF or another listed ebook format with Calibre. Unsupported pairs are rejected by the API rather than presented as working options. Each file is limited to 50 MB; each batch accepts up to 20 files and 100 MB total, with a 200 MB output limit and a 50-million-pixel image-processing limit. Multi-file or multi-page image results are packaged in a ZIP. Office and ebook conversion can change layout; review output before downloading.
- **Add Password:** encrypt a PDF with an open password between 8 and 127 UTF-8 bytes using AES-256. Optionally restrict printing, copying, editing, and form filling. Set an owner password if you need to change permissions later. PDF permission enforcement depends on the viewer and is not a substitute for access control.
- **Unlock PDF:** remove password encryption when you provide the correct password and are authorized to access the document.
- **Unlock PDF Forms:** clear read-only flags on form fields; this does not bypass PDF encryption or field-level cryptographic signatures.
- **Change Permissions:** re-encrypt an authorized PDF with selected printing, copying, editing, and form-filling permissions. You can optionally set an open password; an owner password is required.
- **Remove Pages:** remove selected pages by page number or range. At least one page must remain.
- **Remove Blank Pages:** detect pages that render nearly white and remove them. Faint content, scans, or unusual page backgrounds may need manual review.
- **Remove Annotations:** remove comments, markups, and other page annotations.
- **Remove Images:** remove embedded page images while preserving text and vector graphics.
- **Remove Password:** remove encryption after the correct password is supplied. This is the same authorized unlock flow as **Unlock PDF**.
- **Remove Certificate Sign:** remove signature form fields. Rewriting the document invalidates existing digital signatures; it does not erase the signature appearance if that appearance is part of page content.
- **Auto Form Detection:** detect rectangular boxes and add editable text or checkbox widgets. This heuristic does not read nearby labels or guarantee correct fields; review the PDF in a form-capable viewer.
- **Auto Rename PDF File:** use the PDF title metadata or first non-empty text line for the downloaded filename.
- **Automate:** apply up to five selected cleanup steps in sequence: repair, remove blank pages, remove annotations, remove images, and compression. A selected step that cannot run on the document reports an error instead of being silently skipped.
- **Add Text / Annotate:** insert text or a sticky-note comment at a page position in PDF points (72 points per inch). Text insertion is limited to one selected page.
- **Add Image:** place a PNG or JPEG on one page at the specified position and size in PDF points.
- **Form Editor:** load existing interactive form fields and update their values. This edits existing fields; it does not create, move, or resize them.
- **Adjust Colors / Contrast:** adjust brightness and contrast. Pages are rasterized at up to 120 DPI, so the resulting text is not selectable or searchable.
- **Repair PDF:** rebuild a readable PDF structure. This cannot recover content that is missing or unrecoverable.
- **Detect & Split Scanned Photos:** use image and edge-region heuristics to split a scanned page into photo crops. Review output; photo boundaries and crop accuracy are not guaranteed.
- **Overlay PDFs:** place the second PDF over the first. The overlay must contain one page or the same number of pages as the base.
- **Replace / Invert Color:** replace pixels near a selected color or invert colors. Pages are rasterized at up to 120 DPI; text is no longer selectable or searchable.
- **Personal signature:** draw with adjustable ink colors and pen widths, type, or upload a signature image, then place it at the bottom of a selected page. Undo and clear drawing strokes, save named signatures, choose a default, and reuse or delete saved signatures. Saved images are Fernet-encrypted per account in PostgreSQL. This is a visible image, not a cryptographic signature or identity verification.
- **Certificate signature:** create an invisible cryptographic PDF signature with a PKCS#12 `.p12` or `.pfx` certificate containing its private key. The certificate and password are used transiently and are not stored or logged. PDF Workplace does not verify the certificate chain, add a trusted timestamp, or guarantee that recipients will trust the signer; validate the result with your PDF signature viewer and certificate authority.
- **Signing requests:** email-based requests and collaborative signing sessions are not enabled.
- **Server folders:** create private per-account folders, upload PDFs, review and download stored files, delete them, and run a tool against the folder. Folder merge returns one PDF; document-count splitting groups the folder's PDFs, while other supported operations return a ZIP containing one result per source PDF. Direct uploads remain temporary.

Uploads are limited by `MAX_UPLOAD_BYTES`, `MAX_TOTAL_UPLOAD_BYTES`, and `MAX_PDF_PAGES`; image conversion also enforces pixel limits. Personal signature images are limited to 2 MB, PKCS#12 certificates to 5 MB, and each user may save up to 20 signatures. Encrypted folder contents have a per-user quota set by `MAX_STORAGE_BYTES_PER_USER` (default 1 GiB), with a maximum of 100 folders and 100 PDFs per folder. Folder names, filenames, and saved-signature names remain visible as database metadata; only file contents and saved-signature images are encrypted. This application is not a substitute for malware scanning, backups, or review of sensitive documents. Use HTTPS for any shared deployment, especially when submitting passwords.

## Developer tools

The workspace provides a JavaScript source viewer, FastAPI OpenAPI documentation at `/docs`, local SSO and air-gapped setup guides, and five selectable color themes (Dark, Light, Ocean, Forest, and Rose). The selected theme is saved in the current browser. Server folders are encrypted upload storage and can be processed on demand; the app does not watch arbitrary host directories or run automated background folder scans.

## Project layout

- `frontend/` — static browser UI
- `backend/app/main.py` — API, auth routes, and static frontend hosting
- `backend/app/pdf_ops.py` — PDF transformations
- `backend/app/signing.py` — personal-image and certificate-based PDF signing
- `backend/app/models.py` and `backend/app/db.py` — PostgreSQL persistence
- `docker-compose.yml` — app and PostgreSQL services

## Dependency licensing

This project does not include Stirling PDF source or branding. Its dependencies have their own licenses. In particular, PyMuPDF is offered under AGPL or a commercial license, and the conversion image bundles LibreOffice and Calibre with their own distribution terms. Review all current dependency licenses before distributing or operating a modified/combined service. Obtain legal review if the deployment is commercial or closed-source.
