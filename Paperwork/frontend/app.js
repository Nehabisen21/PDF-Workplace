const fields = document.querySelector("#operation-fields");
const form = document.querySelector("#process-form");
const status = document.querySelector("#status");
const fileInput = document.querySelector("#files");
const submitButton = document.querySelector("#submit-button");
const dropZone = document.querySelector("#drop-zone");
const foldersDialog = document.querySelector("#folders-dialog");
const toolButtons = [...document.querySelectorAll(".tool-button")];
const selectedFiles = [];
let activeOperation = "merge";
let activeFolder = null;
let folderDocuments = [];
let pendingResult = null;
let resultPreviewUrl = null;
let resultPreviewRequest = 0;
let signatureHasContent = false;
let refreshSavedSignatures = null;
let cropPreviewRequest = 0;
let signedInUser = null;
let adminOffset = 0;
let adminTotal = 0;
let adminTeams = [];
let adminTeamFilter = null;
const adminPageSize = 50;
const themeNames = {
  dark: "Dark",
  light: "Light",
  ocean: "Ocean",
  forest: "Forest",
  rose: "Rose",
};
const conversionFormatGroups = [
  { label: "PDF", formats: ["pdf"] },
  { label: "Documents", formats: ["docx", "doc", "odt"] },
  { label: "Comic books", formats: ["cbz", "cbr"] },
  { label: "Spreadsheets", formats: ["xlsx", "xls", "ods"] },
  { label: "Presentations", formats: ["pptx", "ppt", "odp"] },
  {
    label: "Images",
    formats: ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp", "svg"],
  },
  { label: "Email", formats: ["eml", "msg"] },
  { label: "Ebooks", formats: ["epub", "mobi", "azw3", "fb2"] },
];
const conversionTargets = {
  pdf: ["png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "cbz"],
  svg: ["pdf", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"],
  docx: ["pdf", "docx", "doc", "odt"],
  doc: ["pdf", "docx", "doc", "odt"],
  odt: ["pdf", "docx", "doc", "odt"],
  xlsx: ["pdf", "xlsx", "xls", "ods"],
  xls: ["pdf", "xlsx", "xls", "ods"],
  ods: ["pdf", "xlsx", "xls", "ods"],
  pptx: ["pdf", "pptx", "ppt", "odp"],
  ppt: ["pdf", "pptx", "ppt", "odp"],
  odp: ["pdf", "pptx", "ppt", "odp"],
  cbz: ["pdf"],
  cbr: ["pdf", "cbz"],
  eml: ["pdf"],
  msg: ["pdf"],
  epub: ["pdf", "epub", "mobi", "azw3", "fb2"],
  mobi: ["pdf", "epub", "mobi", "azw3", "fb2"],
  azw3: ["pdf", "epub", "mobi", "azw3", "fb2"],
  fb2: ["pdf", "epub", "mobi", "azw3", "fb2"],
};
for (const format of ["jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp"]) {
  conversionTargets[format] = ["pdf", "jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp"];
}
const developerGuides = {
  "folder-scan": {
    title: "Automated Folder Scanning",
    paragraphs: [
      "The current app does not watch arbitrary server directories or run background scans. Server folders contain PDFs explicitly uploaded by a signed-in user; their contents are encrypted in the database.",
      "To process stored documents, open Server folders, select a folder, and run a PDF tool. Unattended host-folder scanning is not enabled.",
    ],
  },
  sso: {
    title: "SSO Guide",
    paragraphs: [
      "Register a Microsoft Entra ID web application and set its redirect URI to your public app URL followed by /auth/callback. Configure the tenant ID, client ID, and client secret in the server's .env file.",
      "Set Assignment required? to Yes on the Enterprise Application and assign only approved users or groups. Set INITIAL_ADMIN_EMAILS for organization owners. See README.md for the full deployment checklist.",
    ],
  },
  "air-gapped": {
    title: "Air-gapped Setup",
    paragraphs: [
      "The PDF service and database run locally, but this app supports Microsoft Entra sign-in only, which requires network access to Microsoft identity endpoints. New SSO logins cannot complete in a fully disconnected environment.",
      "For an offline deployment, preload and verify the container images and dependencies and configure secrets locally. Supporting offline users with an on-premises identity provider or local accounts requires additional authentication integration; the current local login is for one administrator only.",
    ],
  },
};

const toolDetails = {
  merge: {
    title: "Merge PDFs",
    description: "Combine two or more documents into one PDF.",
    fields: [],
  },
  split: {
    title: "Split PDF",
    description: "Split a PDF using page ranges, document structure, or size.",
    fields: [],
  },
  crop: {
    title: "Crop PDF",
    description: "Select an area of the first page to keep on every page.",
    fields: [],
  },
  rotate: {
    title: "Rotate pages",
    description: "Rotate every page in your document.",
    fields: [{ name: "degrees", label: "Rotation", type: "select", options: ["90", "180", "270"] }],
  },
  reorder: {
    title: "Reorder pages",
    description: "Set a new order for all pages.",
    fields: [{ name: "pages", label: "New page order", placeholder: "3,1,2", help: "List every page exactly once." }],
  },
  extract: {
    title: "Extract pages",
    description: "Keep only the pages you need.",
    fields: [{ name: "pages", label: "Pages to extract", placeholder: "1-3,5", help: "Use commas and ranges, e.g. 1-3,5." }],
  },
  compress: {
    title: "Compress PDF",
    description: "Optimize PDF content and remove duplicate objects.",
    fields: [],
  },
  watermark: {
    title: "Add watermark",
    description: "Place centered text on each page.",
    fields: [{ name: "watermark_text", label: "Watermark text", placeholder: "INTERNAL" }],
  },
  stamp: {
    title: "Add Stamp to PDF",
    description: "Add a text stamp, image stamp, or both to every page.",
    fields: [],
  },
  sanitize: {
    title: "Sanitize",
    description: "Remove document metadata, attachments, links, comments, and active content.",
    fields: [],
  },
  flatten: {
    title: "Flatten",
    description: "Make form fields and annotations static while preserving page text.",
    fields: [],
  },
  redact: {
    title: "Redact text",
    description: "Remove matching text and cover its locations.",
    fields: [{ name: "redact_text", label: "Text to redact", placeholder: "Enter exact text" }],
  },
  images_to_pdf: {
    title: "Images to PDF",
    description: "Turn PNG and JPEG images into a multi-page PDF.",
    fields: [],
  },
  pdf_to_images: {
    title: "PDF to images",
    description: "Export every page as a PNG image in a ZIP file.",
    fields: [],
  },
  convert: {
    title: "Convert files",
    description:
      "Convert between supported document, image, comic, email, and ebook formats.",
    fields: [],
  },
  protect: {
    title: "Add Password",
    description: "Add an open password and optionally restrict document actions.",
    fields: [
      {
        name: "protect_password",
        label: "Password to open",
        type: "password",
        placeholder: "At least 8 characters",
        help: "The password is used to encrypt the downloaded file and is not stored.",
      },
      {
        name: "owner_password",
        label: "Owner password (optional)",
        type: "password",
        required: false,
        placeholder: "Leave blank to generate one",
        help: "Set this to change restrictions later. If blank, a random owner password is generated and cannot be recovered.",
      },
    ],
  },
  unlock: {
    title: "Unlock PDF",
    description: "Remove password encryption from a PDF you are authorized to access.",
    fields: [
      {
        name: "unlock_password",
        label: "Current PDF password",
        type: "password",
        autocomplete: "current-password",
        placeholder: "Enter the current password",
      },
    ],
  },
  personal_signature: {
    title: "Personal signature",
    description: "Draw, type, or upload a signature and place it visibly on a PDF.",
    fields: [],
  },
  digital_signature: {
    title: "Certificate signature",
    description: "Create a cryptographic PDF signature with a PKCS#12 certificate.",
    fields: [
      {
        name: "certificate_file",
        label: "PKCS#12 certificate (.p12 or .pfx)",
        type: "file",
        accept: ".p12,.pfx,application/x-pkcs12",
      },
      {
        name: "certificate_password",
        label: "Certificate password",
        type: "password",
        autocomplete: "current-password",
        maxLength: 1024,
        placeholder: "Enter the certificate password",
        help: "The certificate and password are used only during this request and are not stored.",
      },
    ],
  },
  unlock_forms: {
    title: "Unlock PDF Forms",
    description: "Make read-only form fields editable; this does not remove PDF encryption.",
    fields: [
      {
        name: "form_password",
        label: "Current PDF password (if required)",
        type: "password",
        required: false,
        autocomplete: "current-password",
        placeholder: "Enter the password for an encrypted PDF",
      },
    ],
  },
  change_permissions: {
    title: "Change Permissions",
    description: "Encrypt the PDF with the selected allowed actions.",
    fields: [
      {
        name: "source_password",
        label: "Current owner password (if already protected)",
        type: "password",
        required: false,
        maxLength: 127,
        autocomplete: "current-password",
      },
      {
        name: "protect_password",
        label: "Password to open (optional)",
        type: "password",
        required: false,
        placeholder: "Leave blank to allow opening without a password",
      },
      {
        name: "owner_password",
        label: "New owner password",
        type: "password",
        placeholder: "At least 8 characters",
        help: "The owner password is required to change these permissions later.",
      },
    ],
  },
  remove_pages: {
    title: "Remove Pages",
    description: "Remove selected page numbers; at least one page must remain.",
    fields: [{ name: "pages", label: "Pages to remove", placeholder: "2,4-6", help: "Use page numbers and ranges, e.g. 2,4-6." }],
  },
  remove_blank_pages: {
    title: "Remove Blank Pages",
    description: "Detect and remove visually blank pages. Review the result for faint content.",
    fields: [],
  },
  remove_annotations: {
    title: "Remove Annotations",
    description: "Delete comments, markups, and other PDF annotations.",
    fields: [],
  },
  remove_images: {
    title: "Remove Images",
    description: "Remove embedded images from the PDF while retaining other page content.",
    fields: [],
  },
  remove_password: {
    title: "Remove Password",
    description: "Remove encryption when you provide the correct password and are authorized.",
    fields: [{ name: "unlock_password", label: "Current PDF password", type: "password", autocomplete: "current-password" }],
  },
  remove_certificate_sign: {
    title: "Remove Certificate Sign",
    description: "Remove certificate-signature fields. Rewriting the PDF invalidates existing signatures.",
    fields: [],
  },
  auto_form_detection: {
    title: "Auto Form Detection",
    description: "Detect rectangular form boxes and add editable text or checkbox fields. Detection may need review.",
    fields: [],
  },
  auto_rename: {
    title: "Auto Rename PDF File",
    description: "Name the download using the document title metadata or its first line of text.",
    fields: [],
  },
  automate: {
    title: "Automate",
    description: "Run a selected sequence of safe cleanup steps on one PDF.",
    fields: [{
      name: "automation_steps",
      label: "Cleanup steps (select one or more)",
      type: "select",
      multiple: true,
      options: ["repair", "remove_blank_pages", "remove_annotations", "remove_images", "compress"],
      optionLabels: {
        repair: "Repair PDF",
        remove_blank_pages: "Remove blank pages",
        remove_annotations: "Remove annotations",
        remove_images: "Remove images",
        compress: "Compress PDF",
      },
    }],
  },
  add_text: {
    title: "Add Text",
    description: "Place text at a page position measured in PDF points (72 points = 1 inch).",
    fields: [
      { name: "text", label: "Text", type: "textarea", placeholder: "Text to add" },
      { name: "page_number", label: "Page number", type: "number", min: 1, step: 1, value: 1 },
      { name: "x", label: "Horizontal position (points)", type: "number", min: 0, step: 1, value: 72 },
      { name: "y", label: "Vertical position (points)", type: "number", min: 0, step: 1, value: 72 },
      { name: "font_size", label: "Font size (4–96 pt)", type: "number", min: 4, max: 96, step: 1, value: 12 },
      { name: "text_color", label: "Text color", type: "color", value: "#222222" },
    ],
  },
  add_image: {
    title: "Add Image",
    description: "Place a PNG or JPEG image on one page using coordinates and dimensions in PDF points.",
    fields: [
      { name: "tool_image", label: "Image file (PNG or JPEG)", type: "file", accept: "image/png,image/jpeg,.png,.jpg,.jpeg" },
      { name: "page_number", label: "Page number", type: "number", min: 1, step: 1, value: 1 },
      { name: "x", label: "Horizontal position (points)", type: "number", min: 0, step: 1, value: 72 },
      { name: "y", label: "Vertical position (points)", type: "number", min: 0, step: 1, value: 72 },
      { name: "width", label: "Width (points)", type: "number", min: 1, step: 1, value: 144 },
      { name: "height", label: "Height (points)", type: "number", min: 1, step: 1, value: 100 },
    ],
  },
  annotate: {
    title: "Annotate",
    description: "Add a sticky-note comment at the selected page position.",
    fields: [
      { name: "annotation_text", label: "Comment", type: "textarea", placeholder: "Annotation text" },
      { name: "page_number", label: "Page number", type: "number", min: 1, step: 1, value: 1 },
      { name: "x", label: "Horizontal position (points)", type: "number", min: 0, step: 1, value: 72 },
      { name: "y", label: "Vertical position (points)", type: "number", min: 0, step: 1, value: 72 },
    ],
  },
  form_editor: {
    title: "Form Editor",
    description: "Load existing interactive form fields, update their values, and download the edited PDF.",
    fields: [],
  },
  adjust_colors: {
    title: "Adjust Colors / Contrast",
    description: "Adjust brightness and contrast. Pages are rasterized, so text will no longer be selectable.",
    fields: [
      { name: "brightness", label: "Brightness (0.1–3)", type: "number", min: 0.1, max: 3, step: 0.1, value: 1 },
      { name: "contrast", label: "Contrast (0.1–3)", type: "number", min: 0.1, max: 3, step: 0.1, value: 1 },
    ],
  },
  repair: {
    title: "Repair PDF",
    description: "Rebuild a readable PDF structure. This cannot recover content that is missing or unrecoverable.",
    fields: [],
  },
  split_scanned_photos: {
    title: "Detect & Split Scanned Photos",
    description: "Detect separated photo regions and create one page per detected region. Review the results.",
    fields: [],
  },
  overlay: {
    title: "Overlay PDFs",
    description: "Place the second PDF over the first. The overlay must have one page or match the base page count.",
    fields: [],
  },
  color_transform: {
    title: "Replace / Invert Color",
    description: "Replace a chosen color or invert all colors. Pages are rasterized and text will no longer be selectable.",
    fields: [
      { name: "color_mode", label: "Color operation", type: "select", options: ["invert", "replace"], optionLabels: { invert: "Invert colors", replace: "Replace a color" } },
      { name: "source_color", label: "Source color", type: "color", value: "#000000" },
      { name: "replacement_color", label: "Replacement color", type: "color", value: "#FFFFFF" },
      { name: "color_tolerance", label: "Color tolerance (0–255)", type: "number", min: 0, max: 255, step: 1, value: 16 },
    ],
  },
  scale_pages: {
    title: "Scale Pages",
    description: "Resize pages by a scale factor or to a named paper size.",
    fields: [
      { name: "page_size", label: "Page size", type: "select", options: ["KEEP", "A3", "A4", "A5", "LETTER", "LEGAL", "TABLOID"], optionLabels: { KEEP: "Keep current size", A3: "A3", A4: "A4", A5: "A5", LETTER: "Letter", LEGAL: "Legal", TABLOID: "Tabloid" } },
      { name: "scale_factor", label: "Scale factor (e.g. 1.25)", type: "number", min: 0.1, max: 10, step: 0.05, value: 1 },
    ],
  },
  booklet_imposition: {
    title: "Booklet Imposition",
    description: "Reorder pages for booklet printing and place two pages per sheet.",
    fields: [
      { name: "add_border", label: "Add borders around each page", type: "checkbox" },
      { name: "spine_location", label: "Spine location", type: "select", options: ["LEFT", "RIGHT"], optionLabels: { LEFT: "Left", RIGHT: "Right" } },
      { name: "add_gutter", label: "Add a gutter margin", type: "checkbox" },
      { name: "gutter_size", label: "Gutter size (points)", type: "number", min: 0, max: 100, step: 1, value: 0 },
      { name: "double_sided", label: "Double-sided printing", type: "checkbox" },
      { name: "flip_on_short_edge", label: "Flip on short edge", type: "checkbox" },
    ],
  },
  poster_pdf: {
    title: "Split for Poster Print",
    description: "Split large or oddly sized pages into a grid of smaller printable pages.",
    fields: [
      { name: "page_size", label: "Target page size", type: "select", options: ["A4", "LETTER", "A3", "A5", "LEGAL", "TABLOID"], optionLabels: { A4: "A4", LETTER: "Letter", A3: "A3", A5: "A5", LEGAL: "Legal", TABLOID: "Tabloid" } },
      { name: "x_factor", label: "Columns (X factor)", type: "number", min: 1, max: 20, step: 1, value: 1 },
      { name: "y_factor", label: "Rows (Y factor)", type: "number", min: 1, max: 20, step: 1, value: 1 },
      { name: "right_to_left", label: "Right-to-left column order", type: "checkbox" },
    ],
  },
};

function setStatus(message, isError = false) {
  status.textContent = message;
  status.classList.toggle("error", isError);
}

async function stageResult(blob, filename, contentType) {
  if (pendingResult) {
    throw new Error("Download or undo the current result before opening another file.");
  }
  const request = ++resultPreviewRequest;
  pendingResult = {
    blob,
    filename,
    contentType: contentType || blob.type || "application/octet-stream",
  };
  const review = document.querySelector("#result-review");
  const previewImage = document.querySelector("#result-preview-image");
  const previewMessage = document.querySelector("#result-preview-message");
  review.hidden = false;
  previewImage.hidden = true;
  previewImage.removeAttribute("src");
  document.querySelector("#result-filename").textContent = filename;
  document.querySelector("#result-file-metadata").textContent =
    `${pendingResult.contentType} · ${formatFileSize(blob.size)}`;
  document.querySelector("#result-created-at").textContent =
    new Date().toLocaleString();
  setStatus("Review your result. It will download only after you select Download.");
  updateRunButton();

  if (pendingResult.contentType.startsWith("image/")) {
    resultPreviewUrl = URL.createObjectURL(blob);
    previewImage.src = resultPreviewUrl;
    previewImage.alt = `Preview of converted image ${filename}`;
    previewImage.hidden = false;
    previewMessage.textContent = "Preview of the converted image.";
    return;
  }

  if (!filename.toLowerCase().endsWith(".pdf")) {
    previewMessage.textContent =
      "A preview is unavailable for this file. Review the file details before downloading.";
    return;
  }

  previewMessage.textContent = "Creating a preview of the first page…";
  const previewRequest = new FormData();
  previewRequest.append("files", blob, filename);
  try {
    const response = await fetch("/api/pdf/preview", {
      method: "POST",
      body: previewRequest,
    });
    if (request !== resultPreviewRequest || !pendingResult) return;
    if (!response.ok) {
      await showRequestError(response, "Could not create a preview of this PDF.");
    }
    const preview = await response.blob();
    if (request !== resultPreviewRequest || !pendingResult) return;
    resultPreviewUrl = URL.createObjectURL(preview);
    previewImage.src = resultPreviewUrl;
    previewImage.alt = `First-page preview of ${filename}`;
    previewImage.hidden = false;
    previewMessage.textContent = "Preview of the first page.";
  } catch (error) {
    if (request !== resultPreviewRequest || !pendingResult) return;
    previewMessage.textContent =
      `Preview unavailable: ${error.message || "the PDF could not be rendered."}`;
  }
}

function clearPendingResult() {
  resultPreviewRequest += 1;
  if (resultPreviewUrl) URL.revokeObjectURL(resultPreviewUrl);
  resultPreviewUrl = null;
  pendingResult = null;
  const previewImage = document.querySelector("#result-preview-image");
  previewImage.hidden = true;
  previewImage.removeAttribute("src");
  document.querySelector("#result-review").hidden = true;
  updateRunButton();
}

function downloadPendingResult() {
  if (!pendingResult) return;
  const { blob, filename } = pendingResult;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
  clearPendingResult();
  setStatus(`Download started: ${filename}`);
}

function showDeveloperGuide(title, paragraphs) {
  const dialog = document.querySelector("#developer-tools-dialog");
  const content = document.querySelector("#developer-tools-dialog-content");
  document.querySelector("#developer-tools-dialog-title").textContent = title;
  content.replaceChildren();
  for (const paragraphText of paragraphs) {
    const paragraph = document.createElement("p");
    paragraph.textContent = paragraphText;
    content.append(paragraph);
  }
  dialog.showModal();
}

function applyTheme(theme) {
  if (!Object.prototype.hasOwnProperty.call(themeNames, theme)) return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme === "light" ? "light" : "dark";
  for (const input of document.querySelectorAll('input[name="color-theme"]')) {
    input.checked = input.value === theme;
  }
}

function initializeTheme() {
  let theme = "dark";
  try {
    const savedTheme = localStorage.getItem("pdf-workplace-theme");
    if (Object.prototype.hasOwnProperty.call(themeNames, savedTheme)) theme = savedTheme;
  } catch (error) {
    console.error("Could not read the saved color theme.", error);
  }
  applyTheme(theme);
}

function openThemeDialog() {
  const theme = document.documentElement.dataset.theme || "dark";
  applyTheme(theme);
  document.querySelector("#theme-status").textContent = "";
  document.querySelector("#theme-dialog").showModal();
}

function setAdminStatus(message, isError = false) {
  const element = document.querySelector("#admin-users-status");
  element.textContent = message;
  element.classList.toggle("error", isError);
}

function setAccountPageStatus(id, message, isError = false) {
  const element = document.querySelector(`#${id}`);
  element.textContent = message;
  element.classList.toggle("error", isError);
}

function formatAdminDate(value) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unknown"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function renderAdminUsers(users) {
  const list = document.querySelector("#admin-users-list");
  list.replaceChildren();
  if (!users.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "admin-empty";
    cell.textContent = "No users match this search.";
    row.append(cell);
    list.append(row);
    return;
  }
  const roleLabels = {
    org_owner: "Org Owner",
    admin: "Admin",
    team_lead: "Team Lead",
    member: "Member",
  };
  for (const user of users) {
    const row = document.createElement("tr");
    row.dataset.userId = user.id;
    row.dataset.userActive = String(user.is_active);

    const person = document.createElement("td");
    const name = document.createElement("div");
    name.className = "admin-person-name";
    name.textContent = user.name || user.email || `User ${user.id}`;
    const email = document.createElement("div");
    email.className = "admin-person-email";
    email.textContent = user.email || "No email from identity provider";
    person.append(name, email);

    const roleCell = document.createElement("td");
    const role = document.createElement("select");
    role.className = "admin-role-select";
    role.setAttribute("aria-label", `Role for ${user.name || user.email || user.id}`);
    for (const [value, labelText] of Object.entries(roleLabels)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = labelText;
      option.selected = value === user.role;
      role.append(option);
    }
    const isSelf = user.id === signedInUser.id;
    const cannotManageAdmin = signedInUser.role !== "org_owner" &&
      ["org_owner", "admin"].includes(user.role);
    role.disabled = isSelf || user.is_bootstrap_owner || cannotManageAdmin;
    if (user.is_bootstrap_owner) role.title = "Managed by INITIAL_ADMIN_EMAILS in the app configuration.";
    roleCell.append(role);

    const teamCell = document.createElement("td");
    const team = document.createElement("select");
    team.className = "admin-team-select";
    team.setAttribute("aria-label", `Team for ${user.name || user.email || user.id}`);
    for (const availableTeam of adminTeams) {
      const option = document.createElement("option");
      option.value = String(availableTeam.id);
      option.textContent = availableTeam.name;
      option.selected = availableTeam.id === user.team_id;
      team.append(option);
    }
    team.disabled = user.is_bootstrap_owner;
    teamCell.append(team);

    const capabilityCell = document.createElement("td");
    const capabilities = document.createElement("div");
    capabilities.className = "admin-capabilities";
    for (const [capability, label] of [["can_edit", "Editor"], ["can_process", "Processor"]]) {
      const wrapper = document.createElement("label");
      wrapper.className = "admin-capability";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = Boolean(user[capability]);
      checkbox.dataset.userCapability = capability;
      checkbox.setAttribute("aria-label", `${label} capability for ${user.name || user.email || user.id}`);
      checkbox.disabled = user.is_bootstrap_owner;
      const text = document.createElement("span");
      text.textContent = label;
      wrapper.append(checkbox, text);
      capabilities.append(wrapper);
    }
    capabilityCell.append(capabilities);

    const lastLogin = document.createElement("td");
    const lastLoginLabel = document.createElement("div");
    lastLoginLabel.className = "admin-last-login";
    lastLoginLabel.textContent = formatAdminDate(user.last_login_at);
    lastLogin.append(lastLoginLabel);

    const statusCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `admin-status-badge ${user.is_active ? "is-active" : "is-inactive"}`;
    badge.textContent = user.is_active ? "Active" : "Deactivated";
    statusCell.append(badge);

    const actions = document.createElement("td");
    const action = document.createElement("button");
    action.type = "button";
    action.className = "button button-secondary admin-action";
    action.dataset.toggleUser = "";
    action.textContent = user.is_active ? "Deactivate" : "Reactivate";
    action.disabled =
      isSelf ||
      user.is_bootstrap_owner ||
      (signedInUser.role !== "org_owner" && ["org_owner", "admin"].includes(user.role));
    if (user.is_bootstrap_owner) action.title = "Managed by INITIAL_ADMIN_EMAILS in the app configuration.";
    actions.append(action);

    row.append(person, teamCell, capabilityCell, roleCell, lastLogin, statusCell, actions);
    list.append(row);
  }
}

function renderAdminInvitations(invitations) {
  const list = document.querySelector("#admin-invitations-list");
  list.replaceChildren();
  document.querySelector("#pending-invitations-count").textContent =
    `${invitations.length} pending`;
  if (!invitations.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "admin-empty";
    cell.textContent = "No pending invitations.";
    row.append(cell);
    list.append(row);
    return;
  }
  for (const invitation of invitations) {
    const row = document.createElement("tr");
    const person = document.createElement("td");
    person.textContent = invitation.email;
    const team = document.createElement("td");
    team.textContent = invitation.team;
    const role = document.createElement("td");
    role.textContent = {
      org_owner: "Org Owner",
      admin: "Admin",
      team_lead: "Team Lead",
      member: "Member",
    }[invitation.role] || invitation.role;
    const capabilities = document.createElement("td");
    capabilities.textContent = [
      invitation.can_edit ? "Editor" : "",
      invitation.can_process ? "Processor" : "",
    ].filter(Boolean).join(" · ") || "No PDF tools";
    const created = document.createElement("td");
    created.textContent = formatAdminDate(invitation.created_at);
    const actions = document.createElement("td");
    const revoke = document.createElement("button");
    revoke.className = "button button-secondary admin-action";
    revoke.type = "button";
    revoke.dataset.revokeInvitation = String(invitation.id);
    revoke.textContent = "Revoke";
    actions.append(revoke);
    row.append(person, team, role, capabilities, created, actions);
    list.append(row);
  }
}

async function loadAdminUsers() {
  setAdminStatus("Loading users…");
  const queryValues = {
    offset: String(adminOffset),
    limit: String(adminPageSize),
    search: document.querySelector("#admin-user-search").value.trim(),
  };
  if (adminTeamFilter !== null) queryValues.team_id = String(adminTeamFilter);
  const query = new URLSearchParams(queryValues);
  const response = await fetch(`/api/admin/users?${query}`);
  if (!response.ok) return await showRequestError(response, "Could not load users.");
  const result = await response.json();
  adminTotal = result.total;
  renderAdminUsers(result.users);
  document.querySelector("#admin-user-count").textContent =
    `${result.active_count} active / ${result.total} accounts · No seat limit`;
  const page = Math.floor(adminOffset / adminPageSize) + 1;
  const pageCount = Math.max(1, Math.ceil(adminTotal / adminPageSize));
  document.querySelector("#admin-page-label").textContent = `Page ${page} of ${pageCount}`;
  document.querySelector("#admin-previous-page").disabled = adminOffset === 0;
  document.querySelector("#admin-next-page").disabled =
    adminOffset + result.users.length >= adminTotal;
  setAdminStatus("");
}

async function loadAdminTeams() {
  const response = await fetch("/api/admin/teams");
  if (!response.ok) return await showRequestError(response, "Could not load teams.");
  adminTeams = await response.json();

  const filters = document.querySelector("#admin-team-filters");
  filters.replaceChildren();
  const addFilter = (teamId, label) => {
    const button = document.createElement("button");
    button.className = "admin-team-filter";
    button.type = "button";
    button.dataset.teamFilter = teamId === null ? "" : String(teamId);
    button.textContent = label;
    button.classList.toggle(
      "is-selected",
      teamId === adminTeamFilter,
    );
    filters.append(button);
  };
  addFilter(null, "All users");
  for (const team of adminTeams) {
    addFilter(team.id, `${team.name} · ${team.member_count}`);
  }

  const invitationTeam = document.querySelector("#invitation-team");
  invitationTeam.replaceChildren();
  for (const team of adminTeams) {
    const option = document.createElement("option");
    option.value = String(team.id);
    option.textContent = team.name;
    invitationTeam.append(option);
  }
}

async function loadAdminInvitations() {
  const response = await fetch("/api/admin/invitations");
  if (!response.ok) return await showRequestError(response, "Could not load invitations.");
  renderAdminInvitations(await response.json());
}

async function loadAdminManagement() {
  await loadAdminTeams();
  await Promise.all([loadAdminUsers(), loadAdminInvitations()]);
}

async function updateAdminUser(userId, changes) {
  setAdminStatus("Saving user access…");
  const response = await fetch(`/api/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  if (!response.ok) return await showRequestError(response, "Could not update this user.");
  try {
    await loadAdminTeams();
    await loadAdminUsers();
  } catch (error) {
    setAdminStatus(`User access was saved, but the list could not be refreshed: ${error.message}`, true);
    return;
  }
  setAdminStatus("User access updated.");
}

async function loadServerSettings() {
  setAccountPageStatus("server-settings-status", "");
  const response = await fetch("/api/admin/server-settings");
  if (!response.ok) return await showRequestError(response, "Could not load server settings.");
  const settings = await response.json();
  const labels = {
    identity_provider: "Identity provider",
    encrypted_folder_storage: "Encrypted folder storage",
    public_base_url: "Public application URL",
    max_upload_mb: "Maximum file size",
    max_batch_mb: "Maximum batch size",
    max_pdf_pages: "Maximum pages per PDF",
    user_seat_limit: "User seats",
  };
  const list = document.querySelector("#server-settings-list");
  list.replaceChildren();
  for (const [key, label] of Object.entries(labels)) {
    const row = document.createElement("div");
    row.className = "settings-row";
    const term = document.createElement("dt");
    term.textContent = label;
    const value = document.createElement("dd");
    value.textContent =
      typeof settings[key] === "boolean"
        ? settings[key] ? "Enabled" : "Not configured"
        : key.endsWith("_mb")
          ? `${settings[key]} MB`
          : key === "max_pdf_pages"
            ? Number(settings[key]).toLocaleString()
            : String(settings[key]);
    row.append(term, value);
    list.append(row);
  }
  document.querySelector("#server-settings-note").textContent =
    settings.configuration_note;
}

function formatUsageBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

async function loadUsage() {
  setAccountPageStatus("usage-status", "");
  const response = await fetch("/api/usage");
  if (!response.ok) return await showRequestError(response, "Could not load usage.");
  const usage = await response.json();
  document.querySelector("#usage-scope").textContent =
    usage.scope === "organization" ? "Organization activity." : "Your account activity.";
  const metrics = [
    ["Completed PDF actions", usage.completed_operations],
    ["Private folders", usage.folders],
    ["Stored PDFs", usage.stored_pdfs],
    ["Stored data", formatUsageBytes(usage.stored_bytes)],
  ];
  if (usage.active_users !== null) {
    metrics.push(
      ["Active users", usage.active_users],
      ["Teams", usage.teams],
      ["Pending invitations", usage.pending_invitations],
    );
  }
  const container = document.querySelector("#usage-metrics");
  container.replaceChildren();
  for (const [label, value] of metrics) {
    const metric = document.createElement("div");
    metric.className = "usage-metric";
    const title = document.createElement("span");
    title.textContent = label;
    const amount = document.createElement("strong");
    amount.textContent = typeof value === "number" ? value.toLocaleString() : String(value);
    metric.append(title, amount);
    container.append(metric);
  }
}

function setWorkspaceView(destination) {
  const workspace = document.querySelector("#workspace");
  const isAdminView = destination === "admin";
  const isServerSettings = destination === "server-settings";
  const requiresAdmin = isAdminView || isServerSettings;
  if (requiresAdmin && !["org_owner", "admin"].includes(signedInUser?.role)) return;
  workspace.dataset.view = ["admin", "server-settings", "usage"].includes(destination)
    ? destination
    : "files";
  document.querySelector("#admin-view").hidden = !isAdminView;
  document.querySelector("#server-settings-view").hidden = !isServerSettings;
  document.querySelector("#usage-view").hidden = destination !== "usage";
  document.querySelectorAll(".rail-button").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.destination === destination);
  });
  if (isAdminView) {
    adminOffset = 0;
    loadAdminManagement().catch((error) =>
      setAdminStatus(error.message || "Could not load users.", true),
    );
  }
  if (isServerSettings) {
    loadServerSettings().catch((error) =>
      setAccountPageStatus(
        "server-settings-status",
        error.message || "Could not load server settings.",
        true,
      ),
    );
  }
  if (destination === "usage") {
    loadUsage().catch((error) =>
      setAccountPageStatus("usage-status", error.message || "Could not load usage.", true),
    );
  }
}

function renderFields() {
  fields.replaceChildren();
  signatureHasContent = false;
  if (activeOperation === "convert") {
    renderConversionFields();
    return;
  }
  if (activeOperation === "split") {
    renderSplitFields();
    return;
  }
  if (activeOperation === "crop") {
    renderCropFields();
    return;
  }
  if (activeOperation === "stamp") {
    renderStampFields();
    return;
  }
  if (activeOperation === "form_editor") {
    renderFormEditorFields();
    return;
  }
  for (const definition of toolDetails[activeOperation].fields) {
    const wrapper = document.createElement("div");
    wrapper.className = "dynamic-field";
    const label = document.createElement("label");
    label.className = "field-label";
    label.htmlFor = `field-${definition.name}`;
    label.textContent = definition.label;
    let input;
    if (definition.type === "select") {
      input = document.createElement("select");
      input.multiple = Boolean(definition.multiple);
      for (const value of definition.options) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = definition.optionLabels?.[value] || `${value}° clockwise`;
        input.append(option);
      }
    } else if (definition.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = 3;
      input.placeholder = definition.placeholder || "";
      input.className = "text-field";
      input.required = definition.required !== false;
    } else {
      input = document.createElement("input");
      input.type = definition.type || "text";
      if (definition.placeholder) input.placeholder = definition.placeholder;
      input.required = definition.required !== false && definition.type !== "checkbox";
      input.autocomplete =
        definition.autocomplete || (definition.type === "password" ? "new-password" : "off");
      if (definition.type !== "color") input.className = "text-field";
      if (definition.accept) input.accept = definition.accept;
      if (definition.maxLength) input.maxLength = definition.maxLength;
      if (definition.min !== undefined) input.min = String(definition.min);
      if (definition.max !== undefined) input.max = String(definition.max);
      if (definition.step !== undefined) input.step = String(definition.step);
      if (definition.value !== undefined) input.value = String(definition.value);
      if (["protect_password", "owner_password"].includes(definition.name)) {
        input.minLength = 8;
        input.maxLength = 127;
      }
    }
    if (definition.multiple) input.size = Math.min(5, definition.options.length);
    input.id = `field-${definition.name}`;
    input.name = definition.name;
    input.addEventListener("change", updateRunButton);
    wrapper.append(label, input);
    if (definition.help) {
      const help = document.createElement("small");
      help.className = "field-help";
      help.textContent = definition.help;
      wrapper.append(help);
    }
    fields.append(wrapper);
  }
  if (["protect", "change_permissions"].includes(activeOperation)) {
    renderPermissionControls();
  }
  if (activeOperation === "personal_signature") renderPersonalSignatureFields();
}

function renderConversionFields() {
  const sourceField = document.createElement("div");
  sourceField.className = "dynamic-field";
  const sourceLabel = document.createElement("label");
  sourceLabel.className = "field-label";
  sourceLabel.htmlFor = "field-source_format";
  sourceLabel.textContent = "Convert from";
  const sourceSelect = document.createElement("select");
  sourceSelect.id = "field-source_format";
  sourceSelect.name = "source_format";
  for (const group of conversionFormatGroups) {
    const optionGroup = document.createElement("optgroup");
    optionGroup.label = group.label;
    for (const format of group.formats) {
      const option = document.createElement("option");
      option.value = format;
      option.textContent = format.toUpperCase();
      optionGroup.append(option);
    }
    sourceSelect.append(optionGroup);
  }
  sourceField.append(sourceLabel, sourceSelect);

  const targetField = document.createElement("div");
  targetField.className = "dynamic-field";
  const targetLabel = document.createElement("label");
  targetLabel.className = "field-label";
  targetLabel.htmlFor = "field-target_format";
  targetLabel.textContent = "Convert to";
  const targetSelect = document.createElement("select");
  targetSelect.id = "field-target_format";
  targetSelect.name = "target_format";
  targetField.append(targetLabel, targetSelect);

  const note = document.createElement("small");
  note.className = "field-help";
  note.textContent =
    "Choose one source format per batch. Office and ebook conversion may take longer; email attachments are not included.";
  fields.append(sourceField, targetField, note);
  sourceSelect.addEventListener("change", () => {
    updateConversionTargets();
    updateFileAcceptance();
    const validationError = selectedFiles.length ? fileValidationError() : "";
    setStatus(validationError, Boolean(validationError));
    updateRunButton();
  });
  targetSelect.addEventListener("change", updateRunButton);
  updateConversionTargets();
  updateFileAcceptance();
}

function updateConversionTargets() {
  const sourceSelect = document.querySelector("#field-source_format");
  const targetSelect = document.querySelector("#field-target_format");
  if (!sourceSelect || !targetSelect) return;
  const targets = availableConversionTargets(sourceSelect.value);
  const previous = targetSelect.value;
  targetSelect.replaceChildren();
  for (const format of targets) {
    const option = document.createElement("option");
    option.value = format;
    option.textContent = format.toUpperCase();
    targetSelect.append(option);
  }
  if (targets.includes(previous)) targetSelect.value = previous;
}

function conversionFileMatches(file, format) {
  const extension = file.name.toLowerCase().split(".").pop();
  const equivalents = {
    jpg: ["jpg", "jpeg"],
    jpeg: ["jpg", "jpeg"],
    tif: ["tif", "tiff"],
    tiff: ["tif", "tiff"],
  };
  return (equivalents[format] || [format]).includes(extension);
}

function availableConversionTargets(sourceFormat) {
  return (conversionTargets[sourceFormat] || []).filter((format) => format !== sourceFormat);
}

function updateFileAcceptance() {
  if (activeFolder) {
    fileInput.accept = "application/pdf,.pdf";
    return;
  }
  if (activeOperation === "convert") {
    const format = document.querySelector("#field-source_format")?.value || "pdf";
    const equivalents = {
      jpg: ["jpg", "jpeg"],
      jpeg: ["jpg", "jpeg"],
      tif: ["tif", "tiff"],
      tiff: ["tif", "tiff"],
    };
    const extensions = equivalents[format] || [format];
    fileInput.accept = extensions.map((extension) => `.${extension}`).join(",");
    document.querySelector("#file-guidance").textContent =
      `.${extensions.join(", .")} files · 50 MB maximum per file`;
    return;
  }
  const imageMode = activeOperation === "images_to_pdf";
  fileInput.accept = imageMode
    ? "image/png,image/jpeg,.png,.jpg,.jpeg"
    : "application/pdf,.pdf";
  document.querySelector("#file-guidance").textContent = imageMode
    ? "PNG or JPEG images · 50 MB maximum per file"
    : "PDF files · 50 MB maximum per file";
}

function renderFormEditorFields() {
  const loadButton = document.createElement("button");
  loadButton.type = "button";
  loadButton.className = "button button-secondary";
  loadButton.textContent = "Load form fields";
  const message = document.createElement("p");
  message.className = "field-help";
  message.textContent = "Choose one PDF first, then load its interactive fields.";
  const editor = document.createElement("div");
  editor.className = "form-editor-fields";
  const values = document.createElement("input");
  values.type = "hidden";
  values.name = "form_values";
  loadButton.addEventListener("click", async () => {
    if (selectedFiles.length !== 1) {
      message.textContent = "Choose exactly one temporary PDF to inspect.";
      return;
    }
    loadButton.disabled = true;
    message.textContent = "Reading form fields…";
    editor.replaceChildren();
    try {
      const request = new FormData();
      request.append("files", selectedFiles[0]);
      const response = await fetch("/api/pdf/form-fields", { method: "POST", body: request });
      if (!response.ok) await showRequestError(response, "Could not read the PDF form fields.");
      const result = await response.json();
      if (!result.fields.length) {
        message.textContent = "No interactive form fields were found in this PDF.";
        return;
      }
      for (const field of result.fields) {
        const row = document.createElement("div");
        row.className = "dynamic-field";
        const label = document.createElement("label");
        label.className = "field-label";
        label.textContent = `${field.name} (page ${field.page})`;
        const input = document.createElement("input");
        input.className = "text-field";
        input.dataset.formField = field.name;
        input.type = field.type.toLowerCase().includes("checkbox") ? "checkbox" : "text";
        if (input.type === "checkbox") input.checked = ["true", "1", "yes", "on"].includes(String(field.value).toLowerCase());
        else input.value = field.value;
        label.htmlFor = `form-field-${editor.childElementCount}`;
        input.id = label.htmlFor;
        input.addEventListener("change", updateRunButton);
        input.addEventListener("input", updateRunButton);
        row.append(label, input);
        editor.append(row);
      }
      message.textContent = `${result.fields.length} form field(s) loaded.`;
      updateRunButton();
    } catch (error) {
      message.textContent = error.message || "Could not read the PDF form fields.";
    } finally {
      loadButton.disabled = false;
    }
  });
  fields.append(loadButton, message, editor, values);
}

function renderPermissionControls() {
  const wrapper = document.createElement("fieldset");
  wrapper.className = "permission-controls";
  const legend = document.createElement("legend");
  legend.textContent = "Restrict actions";
  wrapper.append(legend);
  const actions = [
    ["print", "Allow printing"],
    ["copy", "Allow copying text and images"],
    ["edit", "Allow document editing"],
    ["forms", "Allow filling forms"],
  ];
  for (const [value, labelText] of actions) {
    const label = document.createElement("label");
    label.className = "permission-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.permission = value;
    input.checked = true;
    const text = document.createElement("span");
    text.textContent = labelText;
    label.append(input, text);
    wrapper.append(label);
  }
  fields.append(wrapper);
}

function renderStampFields() {
  const modeWrapper = document.createElement("div");
  modeWrapper.className = "dynamic-field";
  const modeLabel = document.createElement("label");
  modeLabel.className = "field-label";
  modeLabel.htmlFor = "field-stamp_mode";
  modeLabel.textContent = "Stamp type";
  const mode = document.createElement("select");
  mode.id = "field-stamp_mode";
  mode.name = "stamp_mode";
  for (const [value, labelText] of [
    ["text", "Text stamp"],
    ["image", "Image stamp"],
    ["both", "Text and image"],
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labelText;
    mode.append(option);
  }
  modeWrapper.append(modeLabel, mode);
  fields.append(modeWrapper);

  const textWrapper = document.createElement("div");
  textWrapper.className = "dynamic-field";
  const textLabel = document.createElement("label");
  textLabel.className = "field-label";
  textLabel.htmlFor = "field-stamp_text";
  textLabel.textContent = "Stamp text";
  const textInput = document.createElement("input");
  textInput.id = "field-stamp_text";
  textInput.name = "stamp_text";
  textInput.className = "text-field";
  textInput.maxLength = 80;
  textInput.placeholder = "e.g. APPROVED";
  textWrapper.append(textLabel, textInput);

  const imageWrapper = document.createElement("div");
  imageWrapper.className = "dynamic-field";
  const imageLabel = document.createElement("label");
  imageLabel.className = "field-label";
  imageLabel.htmlFor = "field-stamp_image";
  imageLabel.textContent = "Stamp image (PNG or JPEG)";
  const imageInput = document.createElement("input");
  imageInput.id = "field-stamp_image";
  imageInput.name = "stamp_image";
  imageInput.type = "file";
  imageInput.accept = "image/png,image/jpeg,.png,.jpg,.jpeg";
  imageInput.className = "text-field";
  imageWrapper.append(imageLabel, imageInput);

  const positionWrapper = document.createElement("div");
  positionWrapper.className = "dynamic-field";
  const positionLabel = document.createElement("label");
  positionLabel.className = "field-label";
  positionLabel.htmlFor = "field-stamp_position";
  positionLabel.textContent = "Position";
  const position = document.createElement("select");
  position.id = "field-stamp_position";
  position.name = "stamp_position";
  for (const [value, labelText] of [
    ["center", "Center"],
    ["top_left", "Top left"],
    ["top_right", "Top right"],
    ["bottom_left", "Bottom left"],
    ["bottom_right", "Bottom right"],
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labelText;
    position.append(option);
  }
  positionWrapper.append(positionLabel, position);
  fields.append(textWrapper, imageWrapper, positionWrapper);

  const updateMode = () => {
    const useText = mode.value !== "image";
    const useImage = mode.value !== "text";
    textWrapper.hidden = !useText;
    textInput.required = useText;
    imageWrapper.hidden = !useImage;
    imageInput.required = useImage;
    updateRunButton();
  };
  mode.addEventListener("change", updateMode);
  textInput.addEventListener("input", updateRunButton);
  imageInput.addEventListener("change", updateRunButton);
  updateMode();
}

function renderSplitFields() {
  const modeWrapper = document.createElement("div");
  modeWrapper.className = "dynamic-field";
  const modeLabel = document.createElement("label");
  modeLabel.className = "field-label";
  modeLabel.htmlFor = "field-split_mode";
  modeLabel.textContent = "Split method";
  const modeSelect = document.createElement("select");
  modeSelect.id = "field-split_mode";
  modeSelect.name = "split_mode";
  const modes = [
    ["pages", "Page Numbers"],
    ["chapters", "Chapters"],
    ["sections", "Sections"],
    ["file_size", "File Size"],
    ["page_count", "Page Count"],
    ["document_count", "Document Count"],
    ["page_divider", "Page Divider"],
    ["printable_chunks", "Printable Chunks"],
  ];
  for (const [value, label] of modes) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    modeSelect.append(option);
  }
  modeWrapper.append(modeLabel, modeSelect);
  const modeFields = document.createElement("div");
  modeFields.id = "split-mode-fields";
  fields.append(modeWrapper, modeFields);

  const renderModeFields = () => {
    modeFields.replaceChildren();
    const addInput = (name, labelText, placeholder, help, settings = {}) => {
      const wrapper = document.createElement("div");
      wrapper.className = "dynamic-field";
      const label = document.createElement("label");
      label.className = "field-label";
      label.htmlFor = `field-${name}`;
      label.textContent = labelText;
      const input = document.createElement("input");
      input.id = `field-${name}`;
      input.name = name;
      input.type = settings.type || "text";
      input.className = "text-field";
      input.placeholder = placeholder;
      input.required = true;
      if (settings.min !== undefined) input.min = String(settings.min);
      if (settings.max !== undefined) input.max = String(settings.max);
      if (settings.step !== undefined) input.step = String(settings.step);
      input.addEventListener("input", updateRunButton);
      wrapper.append(label, input);
      if (help) {
        const note = document.createElement("small");
        note.className = "field-help";
        note.textContent = help;
        wrapper.append(note);
      }
      modeFields.append(wrapper);
    };

    switch (modeSelect.value) {
      case "pages":
        addInput("groups", "Page groups", "1-3;4-6", "Use semicolons between output files.");
        break;
      case "chapters":
        {
          const note = document.createElement("p");
          note.className = "field-help";
          note.textContent =
            'Uses level-1 PDF bookmarks, or page headings beginning with "Chapter".';
          modeFields.append(note);
        }
        break;
      case "sections":
        {
          const note = document.createElement("p");
          note.className = "field-help";
          note.textContent = "Uses level-2 PDF bookmarks, or numbered section headings.";
          modeFields.append(note);
        }
        break;
      case "file_size":
        addInput(
          "split_value",
          "Maximum size per PDF (MB)",
          "10",
          "A single page larger than this limit cannot be split further.",
          { type: "number", min: 0.1, max: 100, step: 0.1 },
        );
        break;
      case "page_count":
        addInput(
          "split_value",
          "Pages per PDF",
          "10",
          "Pages are grouped in order; the final file may contain fewer pages.",
          { type: "number", min: 1, max: 500, step: 1 },
        );
        break;
      case "document_count":
        addInput(
          "split_value",
          "Documents per PDF",
          "1",
          "Provide at least two PDFs. Each output combines this many input documents.",
          { type: "number", min: 1, max: 20, step: 1 },
        );
        break;
      case "page_divider":
        addInput(
          "divider_text",
          "Text on divider pages",
          "Enter text from a divider page",
          "Pages containing this text are removed and mark the start of the next output.",
        );
        break;
      default:
        break;
    }
    updateRunButton();
  };
  modeSelect.addEventListener("change", renderModeFields);
  renderModeFields();
}

function renderCropFields() {
  const wrapper = document.createElement("div");
  wrapper.className = "dynamic-field crop-field";
  const guidance = document.createElement("p");
  guidance.className = "field-help";
  guidance.textContent =
    "Preview the first page, then select an area. The same proportional area is applied to every page.";
  const actions = document.createElement("div");
  actions.className = "crop-actions";
  const selectButton = document.createElement("button");
  selectButton.className = "button button-secondary";
  selectButton.type = "button";
  selectButton.textContent = "Select Crop Area";
  const resetButton = document.createElement("button");
  resetButton.className = "button button-quiet";
  resetButton.type = "button";
  resetButton.textContent = "Reset";
  const preview = document.createElement("div");
  preview.className = "crop-preview";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-label", "First PDF page crop preview");
  canvas.hidden = true;
  const placeholder = document.createElement("p");
  placeholder.className = "field-help";
  placeholder.textContent = "Add one temporary PDF to load its crop preview.";
  preview.append(canvas, placeholder);
  const hiddenFields = ["crop_x", "crop_y", "crop_width", "crop_height"].map((name) => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.id = `field-${name}`;
    return input;
  });
  actions.append(selectButton, resetButton);
  wrapper.append(guidance, actions, preview, ...hiddenFields);
  fields.append(wrapper);

  let previewImage = null;
  let selectionStart = null;
  let selection = null;
  let selecting = false;
  const context = canvas.getContext("2d");
  const drawPreview = () => {
    if (!previewImage) return;
    context.drawImage(previewImage, 0, 0, canvas.width, canvas.height);
    if (!selection) return;
    context.fillStyle = "rgba(0, 0, 0, 0.48)";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.clearRect(selection.x, selection.y, selection.width, selection.height);
    context.drawImage(
      previewImage,
      selection.x,
      selection.y,
      selection.width,
      selection.height,
      selection.x,
      selection.y,
      selection.width,
      selection.height,
    );
    context.strokeStyle = "#a59cff";
    context.lineWidth = Math.max(2, canvas.width / 400);
    context.strokeRect(selection.x, selection.y, selection.width, selection.height);
  };
  const updateCropInputs = () => {
    const values = [
      selection.x / canvas.width,
      selection.y / canvas.height,
      selection.width / canvas.width,
      selection.height / canvas.height,
    ];
    hiddenFields.forEach((input, index) => {
      input.value = values[index].toFixed(6);
    });
    updateRunButton();
  };
  const pointFromEvent = (event) => {
    const bounds = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(canvas.width, (event.clientX - bounds.left) * canvas.width / bounds.width)),
      y: Math.max(0, Math.min(canvas.height, (event.clientY - bounds.top) * canvas.height / bounds.height)),
    };
  };
  selectButton.addEventListener("click", () => {
    if (!previewImage) return setStatus("Add one temporary PDF to select a crop area.", true);
    selecting = true;
    canvas.classList.add("is-selecting");
    setStatus("Drag across the preview to select the area to keep.");
  });
  resetButton.addEventListener("click", () => {
    selection = null;
    hiddenFields.forEach((input) => { input.value = ""; });
    canvas.classList.remove("is-selecting");
    selecting = false;
    drawPreview();
    updateRunButton();
  });
  canvas.addEventListener("pointerdown", (event) => {
    if (!selecting) return;
    event.preventDefault();
    canvas.setPointerCapture(event.pointerId);
    selectionStart = pointFromEvent(event);
    selection = { x: selectionStart.x, y: selectionStart.y, width: 0, height: 0 };
    drawPreview();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!selectionStart || !selecting) return;
    const point = pointFromEvent(event);
    selection = {
      x: Math.min(selectionStart.x, point.x),
      y: Math.min(selectionStart.y, point.y),
      width: Math.abs(point.x - selectionStart.x),
      height: Math.abs(point.y - selectionStart.y),
    };
    drawPreview();
  });
  canvas.addEventListener("pointerup", (event) => {
    if (!selectionStart || !selecting) return;
    const point = pointFromEvent(event);
    selection = {
      x: Math.min(selectionStart.x, point.x),
      y: Math.min(selectionStart.y, point.y),
      width: Math.abs(point.x - selectionStart.x),
      height: Math.abs(point.y - selectionStart.y),
    };
    selectionStart = null;
    selecting = false;
    canvas.classList.remove("is-selecting");
    drawPreview();
    if (selection.width < 4 || selection.height < 4) {
      selection = null;
      setStatus("Select a larger crop area.", true);
      drawPreview();
      return;
    }
    updateCropInputs();
    setStatus("Crop area selected.");
  });

  const request = ++cropPreviewRequest;
  const file = !activeFolder && selectedFiles.length === 1 ? selectedFiles[0] : null;
  if (!file) {
    placeholder.textContent = activeFolder
      ? "Crop preview requires a temporary PDF, not a server-folder document."
      : "Add one temporary PDF to load its crop preview.";
    updateRunButton();
    return;
  }
  placeholder.textContent = "Loading PDF preview…";
  const data = new FormData();
  data.append("files", file);
  fetch("/api/pdf/preview", { method: "POST", body: data })
    .then(async (response) => {
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "Could not load the PDF preview.");
      }
      return response.blob();
    })
    .then((blob) => {
      if (request !== cropPreviewRequest || activeOperation !== "crop") return;
      const image = new Image();
      const url = URL.createObjectURL(blob);
      image.onload = () => {
        URL.revokeObjectURL(url);
        if (request !== cropPreviewRequest || activeOperation !== "crop") return;
        previewImage = image;
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        canvas.hidden = false;
        placeholder.hidden = true;
        drawPreview();
        setStatus("Preview ready. Select a crop area.");
        updateRunButton();
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        if (request === cropPreviewRequest) setStatus("The PDF preview could not be displayed.", true);
      };
      image.src = url;
    })
    .catch((error) => {
      if (request === cropPreviewRequest) setStatus(error.message, true);
    });
}

function renderPersonalSignatureFields() {
  let selectedSavedSignature = null;
  let signatureMode = "draw";
  let inkColor = "#141414";
  let penWidth = 5;
  let strokes = [];
  let currentStroke = null;
  let signatureSelectionTouched = false;
  let savedSelectionRequest = 0;

  const savedWrapper = document.createElement("div");
  savedWrapper.className = "dynamic-field";
  const savedLabel = document.createElement("label");
  savedLabel.className = "field-label";
  savedLabel.htmlFor = "saved-signature-select";
  savedLabel.textContent = "My signatures";
  const savedSelect = document.createElement("select");
  savedSelect.id = "saved-signature-select";
  savedSelect.className = "text-field";
  savedWrapper.append(savedLabel, savedSelect);

  const modeTabs = document.createElement("div");
  modeTabs.className = "signature-tabs";
  modeTabs.setAttribute("role", "group");
  modeTabs.setAttribute("aria-label", "Signature method");
  const modeButtons = {};
  for (const [mode, label] of [
    ["draw", "Draw"],
    ["type", "Type"],
    ["upload", "Upload"],
  ]) {
    const button = document.createElement("button");
    button.className = "signature-tab";
    button.type = "button";
    button.dataset.signatureMode = mode;
    button.textContent = label;
    button.setAttribute("aria-pressed", String(mode === signatureMode));
    modeButtons[mode] = button;
    modeTabs.append(button);
  }
  const editor = document.createElement("div");
  editor.className = "signature-editor";
  const canvas = document.createElement("canvas");
  canvas.id = "signature-canvas";
  canvas.width = 600;
  canvas.height = 180;
  canvas.setAttribute("aria-label", "Signature preview and drawing area");
  const controls = document.createElement("div");
  controls.className = "signature-controls";
  const inkGroup = document.createElement("div");
  inkGroup.className = "signature-control-group";
  const inkLabel = document.createElement("span");
  inkLabel.className = "signature-control-label";
  inkLabel.textContent = "Ink";
  const colorButtons = [];
  for (const [color, label] of [
    ["#141414", "Black ink"],
    ["#17264a", "Navy ink"],
    ["#2454d6", "Blue ink"],
  ]) {
    const button = document.createElement("button");
    button.className = "signature-color";
    button.type = "button";
    button.style.setProperty("--signature-color", color);
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-pressed", String(color === inkColor));
    button.addEventListener("click", () => {
      inkColor = color;
      colorButtons.forEach((item) =>
        item.setAttribute("aria-pressed", String(item === button)),
      );
    });
    colorButtons.push(button);
    inkGroup.append(button);
  }
  inkGroup.prepend(inkLabel);

  const penGroup = document.createElement("div");
  penGroup.className = "signature-control-group";
  const penLabel = document.createElement("span");
  penLabel.className = "signature-control-label";
  penLabel.textContent = "Pen";
  const penButtons = [];
  for (const [width, label] of [
    [3, "Fine pen"],
    [5, "Medium pen"],
    [8, "Thick pen"],
  ]) {
    const button = document.createElement("button");
    button.className = "signature-pen";
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-pressed", String(width === penWidth));
    const sample = document.createElement("span");
    sample.style.width = `${width + 3}px`;
    sample.style.height = `${width + 3}px`;
    button.append(sample);
    button.addEventListener("click", () => {
      penWidth = width;
      penButtons.forEach((item) =>
        item.setAttribute("aria-pressed", String(item === button)),
      );
    });
    penButtons.push(button);
    penGroup.append(button);
  }
  penGroup.prepend(penLabel);

  const drawOptions = document.createElement("div");
  drawOptions.className = "signature-draw-options";
  drawOptions.append(inkGroup, penGroup);
  const canvasHint = document.createElement("small");
  canvasHint.className = "field-help signature-canvas-hint";
  canvasHint.textContent = "Use a mouse, trackpad, pen, or finger.";

  const typedWrapper = document.createElement("label");
  typedWrapper.className = "dynamic-field";
  typedWrapper.htmlFor = "signature-text";
  typedWrapper.textContent = "Signature text";
  const typedInput = document.createElement("input");
  typedInput.id = "signature-text";
  typedInput.className = "text-field";
  typedInput.maxLength = 60;
  typedInput.placeholder = "Type your name";
  typedInput.autocomplete = "name";
  typedWrapper.hidden = true;
  typedWrapper.append(typedInput);
  const uploadWrapper = document.createElement("label");
  uploadWrapper.className = "dynamic-field";
  uploadWrapper.htmlFor = "signature-upload";
  uploadWrapper.textContent = "Signature image";
  const uploadInput = document.createElement("input");
  uploadInput.id = "signature-upload";
  uploadInput.type = "file";
  uploadInput.accept = "image/png,image/jpeg,.png,.jpg,.jpeg";
  uploadInput.className = "text-field";
  uploadWrapper.hidden = true;
  uploadWrapper.append(uploadInput);
  const drawActions = document.createElement("div");
  drawActions.className = "signature-draw-actions";
  const undoButton = document.createElement("button");
  undoButton.className = "button button-secondary";
  undoButton.type = "button";
  undoButton.textContent = "Undo";
  undoButton.disabled = true;
  const clearButton = document.createElement("button");
  clearButton.className = "button button-secondary";
  clearButton.type = "button";
  clearButton.textContent = "Clear signature";
  drawActions.append(undoButton, clearButton);
  controls.append(drawOptions, drawActions, typedWrapper, uploadWrapper);
  editor.append(canvas, canvasHint, controls);

  const savePanel = document.createElement("div");
  savePanel.className = "signature-save-panel";
  const saveLabel = document.createElement("label");
  saveLabel.className = "signature-save-toggle";
  const saveCheckbox = document.createElement("input");
  saveCheckbox.type = "checkbox";
  saveCheckbox.id = "save-to-signatures";
  saveCheckbox.name = "save_to_signatures";
  saveCheckbox.checked = true;
  const saveText = document.createElement("span");
  saveText.textContent = "Save to my signatures";
  saveLabel.append(saveCheckbox, saveText);
  const saveHelp = document.createElement("small");
  saveHelp.className = "field-help";
  saveHelp.textContent = "Encrypted and available for reuse on future documents.";
  const signatureNameLabel = document.createElement("label");
  signatureNameLabel.className = "field-label";
  signatureNameLabel.htmlFor = "signature-name";
  signatureNameLabel.textContent = "Name";
  const signatureName = document.createElement("input");
  signatureName.id = "signature-name";
  signatureName.name = "signature_name";
  signatureName.className = "text-field";
  signatureName.maxLength = 80;
  signatureName.value = "My signature";
  const defaultLabel = document.createElement("label");
  defaultLabel.className = "signature-default-toggle";
  const defaultCheckbox = document.createElement("input");
  defaultCheckbox.type = "checkbox";
  defaultCheckbox.id = "signature-make-default";
  defaultCheckbox.name = "make_default";
  const defaultText = document.createElement("span");
  defaultText.textContent = "Make default";
  defaultLabel.append(defaultCheckbox, defaultText);
  const saveFields = document.createElement("div");
  saveFields.className = "signature-save-fields";
  saveFields.append(signatureNameLabel, signatureName, defaultLabel);
  const deleteSavedButton = document.createElement("button");
  deleteSavedButton.className = "button button-quiet signature-delete-button";
  deleteSavedButton.type = "button";
  deleteSavedButton.textContent = "Delete saved signature";
  deleteSavedButton.hidden = true;
  savePanel.append(saveLabel, saveHelp, saveFields, deleteSavedButton);

  const pageWrapper = document.createElement("div");
  pageWrapper.className = "signature-placement";
  const pageLabel = document.createElement("label");
  pageLabel.className = "field-label";
  pageLabel.htmlFor = "signature-page-number";
  pageLabel.textContent = "Page";
  const pageInput = document.createElement("input");
  pageInput.id = "signature-page-number";
  pageInput.name = "page_number";
  pageInput.type = "number";
  pageInput.min = "1";
  pageInput.step = "1";
  pageInput.value = "1";
  pageInput.required = true;
  pageInput.className = "text-field";
  const positionWrapper = document.createElement("div");
  const positionLabel = document.createElement("label");
  positionLabel.className = "field-label";
  positionLabel.htmlFor = "signature-position";
  positionLabel.textContent = "Position";
  const positionSelect = document.createElement("select");
  positionSelect.id = "signature-position";
  positionSelect.name = "position";
  positionSelect.className = "text-field";
  for (const [value, text] of [
    ["bottom-left", "Bottom left"],
    ["bottom-center", "Bottom center"],
    ["bottom-right", "Bottom right"],
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    positionSelect.append(option);
  }
  pageWrapper.append(pageLabel, pageInput);
  positionWrapper.append(positionLabel, positionSelect);
  pageWrapper.append(positionWrapper);

  const signatureData = document.createElement("input");
  signatureData.type = "hidden";
  signatureData.id = "signature-image-data";
  signatureData.name = "signature_image";
  const savedSignatureId = document.createElement("input");
  savedSignatureId.type = "hidden";
  savedSignatureId.id = "saved-signature-id";
  fields.append(savedWrapper, modeTabs, editor, savePanel, pageWrapper, signatureData, savedSignatureId);

  const context = canvas.getContext("2d");
  context.lineCap = "round";
  context.lineJoin = "round";
  let drawing = false;
  const setCanvasCoordinates = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (canvas.width / rect.width),
      y: (event.clientY - rect.top) * (canvas.height / rect.height),
    };
  };
  const updateSignatureState = () => {
    if (!selectedSavedSignature) {
      signatureData.value = canvas.toDataURL("image/png");
    }
    updateRunButton();
  };
  const redrawStrokes = () => {
    context.clearRect(0, 0, canvas.width, canvas.height);
    for (const stroke of strokes) {
      context.beginPath();
      context.strokeStyle = stroke.color;
      context.lineWidth = stroke.width;
      const [first, ...remaining] = stroke.points;
      context.moveTo(first.x, first.y);
      if (remaining.length) {
        remaining.forEach((point) => context.lineTo(point.x, point.y));
      } else {
        context.lineTo(first.x + 0.1, first.y + 0.1);
      }
      context.stroke();
    }
    signatureHasContent = strokes.length > 0;
    undoButton.disabled = strokes.length === 0;
    updateSignatureState();
  };
  const renderTypedSignature = () => {
    context.clearRect(0, 0, canvas.width, canvas.height);
    const text = typedInput.value.trim();
    if (!text) {
      signatureHasContent = false;
      updateSignatureState();
      return;
    }
    context.fillStyle = inkColor;
    context.font = "italic 54px cursive";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(text, canvas.width / 2, canvas.height / 2, canvas.width - 30);
    signatureHasContent = true;
    updateSignatureState();
  };
  const renderUploadedSignature = () => {
    const file = uploadInput.files[0];
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!file) {
      signatureHasContent = false;
      updateSignatureState();
      return;
    }
    const image = new Image();
    image.onload = () => {
      if (image.width * image.height > 4_000_000) {
        uploadInput.value = "";
        signatureHasContent = false;
        setStatus("The signature image dimensions must be no larger than 4 million pixels.", true);
        updateRunButton();
      } else if (signatureMode === "upload" && !selectedSavedSignature) {
        const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
        const width = image.width * scale;
        const height = image.height * scale;
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
        signatureHasContent = true;
        updateSignatureState();
      }
      URL.revokeObjectURL(image.src);
    };
    image.onerror = () => {
      uploadInput.value = "";
      signatureHasContent = false;
      setStatus("The selected signature image could not be opened.", true);
      updateRunButton();
      URL.revokeObjectURL(image.src);
    };
    image.src = URL.createObjectURL(file);
  };
  const selectMode = (mode) => {
    signatureMode = mode;
    for (const [key, button] of Object.entries(modeButtons)) {
      button.classList.toggle("is-selected", key === mode);
      button.setAttribute("aria-pressed", String(key === mode));
    }
    drawOptions.hidden = mode !== "draw";
    drawActions.hidden = mode !== "draw";
    typedWrapper.hidden = mode !== "type";
    uploadWrapper.hidden = mode !== "upload";
    canvasHint.hidden = mode !== "draw";
    canvas.classList.toggle("is-readonly", mode !== "draw");
    if (mode === "draw") redrawStrokes();
    if (mode === "type") renderTypedSignature();
    if (mode === "upload") renderUploadedSignature();
  };
  const useSavedSignature = async (id) => {
    const requestId = ++savedSelectionRequest;
    if (!id) {
      selectedSavedSignature = null;
      savedSignatureId.removeAttribute("name");
      savedSignatureId.value = "";
      signatureData.value = "";
      modeTabs.hidden = false;
      modeTabs.querySelectorAll("button").forEach((button) => (button.disabled = false));
      canvas.classList.add("is-readonly");
      drawOptions.hidden = true;
      drawActions.hidden = true;
      typedWrapper.hidden = true;
      uploadWrapper.hidden = true;
      canvasHint.hidden = true;
      saveCheckbox.checked = true;
      saveCheckbox.disabled = false;
      signatureName.value = "My signature";
      signatureName.disabled = false;
      defaultCheckbox.checked = false;
      defaultCheckbox.disabled = false;
      deleteSavedButton.hidden = true;
      signatureHasContent = false;
      strokes = [];
      typedInput.value = "";
      uploadInput.value = "";
      context.clearRect(0, 0, canvas.width, canvas.height);
      selectMode(signatureMode);
      return;
    }
    const response = await fetch(`/api/signatures/${id}`);
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "Could not load the saved signature.");
    }
    const saved = await response.json();
    if (requestId !== savedSelectionRequest) return;
    selectedSavedSignature = saved;
    savedSignatureId.name = "saved_signature_id";
    savedSignatureId.value = String(saved.id);
    signatureData.value = "";
    modeTabs.hidden = true;
    drawOptions.hidden = true;
    drawActions.hidden = true;
    typedWrapper.hidden = true;
    uploadWrapper.hidden = true;
    canvasHint.hidden = true;
    canvas.classList.add("is-readonly");
    saveCheckbox.checked = false;
    saveCheckbox.disabled = true;
    signatureName.value = saved.name;
    signatureName.disabled = true;
    defaultCheckbox.checked = saved.is_default;
    defaultCheckbox.disabled = false;
    deleteSavedButton.hidden = false;
    const image = new Image();
    image.onload = () => {
      if (requestId !== savedSelectionRequest) return;
      context.clearRect(0, 0, canvas.width, canvas.height);
      const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
      const width = image.width * scale;
      const height = image.height * scale;
      context.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
      signatureHasContent = true;
      updateRunButton();
    };
    image.onerror = () => setStatus("The saved signature image could not be opened.", true);
    image.src = saved.signature_image;
    updateRunButton();
  };
  const loadSavedSignatures = async (preferredId = null) => {
    const response = await fetch("/api/signatures");
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "Could not load saved signatures.");
    }
    const signatures = await response.json();
    savedSelect.replaceChildren();
    const createOption = document.createElement("option");
    createOption.value = "";
    createOption.textContent = "Create new signature";
    savedSelect.append(createOption);
    for (const saved of signatures) {
      const option = document.createElement("option");
      option.value = String(saved.id);
      option.textContent = `${saved.name}${saved.is_default ? " (Default)" : ""}`;
      savedSelect.append(option);
    }
    const preferred = signatures.find((saved) => String(saved.id) === String(preferredId));
    const current = signatures.find((saved) => String(saved.id) === savedSelect.value);
    const defaultSignature = signatures.find((saved) => saved.is_default);
    const useDefault = !signatureSelectionTouched || (savedSelect.value && !current);
    const signatureToUse = preferred || current || (useDefault ? defaultSignature : null);
    if (signatureToUse) {
      savedSelect.value = String(signatureToUse.id);
      await useSavedSignature(savedSelect.value);
    } else {
      savedSelect.value = "";
      await useSavedSignature("");
    }
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (signatureMode !== "draw" || selectedSavedSignature) return;
    event.preventDefault();
    canvas.setPointerCapture(event.pointerId);
    drawing = true;
    currentStroke = {
      color: inkColor,
      width: penWidth,
      points: [setCanvasCoordinates(event)],
    };
    strokes.push(currentStroke);
    redrawStrokes();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drawing) return;
    currentStroke.points.push(setCanvasCoordinates(event));
    redrawStrokes();
  });
  canvas.addEventListener("pointerup", () => {
    drawing = false;
    currentStroke = null;
    redrawStrokes();
  });
  canvas.addEventListener("pointercancel", () => {
    drawing = false;
    currentStroke = null;
    redrawStrokes();
  });
  Object.entries(modeButtons).forEach(([mode, button]) => {
    button.addEventListener("click", () => selectMode(mode));
  });
  typedInput.addEventListener("input", renderTypedSignature);
  uploadInput.addEventListener("change", () => {
    const file = uploadInput.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      uploadInput.value = "";
      signatureHasContent = false;
      setStatus("The signature image must be no larger than 2 MB.", true);
      updateRunButton();
      return;
    }
    if (signatureMode === "upload") renderUploadedSignature();
  });
  undoButton.addEventListener("click", () => {
    if (!strokes.length) return;
    strokes.pop();
    redrawStrokes();
  });
  clearButton.addEventListener("click", () => {
    context.clearRect(0, 0, canvas.width, canvas.height);
    strokes = [];
    typedInput.value = "";
    uploadInput.value = "";
    signatureHasContent = false;
    undoButton.disabled = true;
    updateSignatureState();
    if (signatureMode === "type") typedInput.focus();
  });
  savedSelect.addEventListener("change", async () => {
    signatureSelectionTouched = true;
    try {
      await useSavedSignature(savedSelect.value);
      setStatus("");
    } catch (error) {
      setStatus(error.message || "Could not load the saved signature.", true);
    }
  });
  saveCheckbox.addEventListener("change", () => {
    signatureName.disabled = !saveCheckbox.checked;
    defaultCheckbox.disabled = !saveCheckbox.checked;
    updateRunButton();
  });
  signatureName.addEventListener("input", updateRunButton);
  defaultCheckbox.addEventListener("change", updateRunButton);
  deleteSavedButton.addEventListener("click", async () => {
    if (!selectedSavedSignature || !window.confirm(`Delete "${selectedSavedSignature.name}"?`)) return;
    try {
      const response = await fetch(`/api/signatures/${selectedSavedSignature.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "Could not delete the saved signature.");
      }
      await loadSavedSignatures();
      setStatus("Saved signature deleted.");
    } catch (error) {
      setStatus(error.message || "Could not delete the saved signature.", true);
    }
  });
  refreshSavedSignatures = loadSavedSignatures;
  selectMode("draw");
  loadSavedSignatures().catch((error) => {
    setStatus(error.message || "Could not load saved signatures.", true);
  });
}

function updateRunButton() {
  submitButton.disabled = Boolean(fileValidationError());
}

function fileValidationError() {
  if (pendingResult) {
    return "Download or undo the reviewed result before processing another PDF.";
  }
  if (!canUseOperation(activeOperation)) {
    return "Your account does not have the capability required for this tool.";
  }
  if (activeFolder && activeOperation === "convert") {
    return "Convert files accepts temporary uploads, not server-folder PDFs.";
  }
  const fileCount = activeFolder ? folderDocuments.length : selectedFiles.length;
  if (
    activeFolder &&
    ["personal_signature", "digital_signature"].includes(activeOperation)
  ) {
    return "Download a PDF from the server folder and add it as a temporary file before signing.";
  }
  if (activeFolder && activeOperation === "crop") {
    return "Crop preview requires a temporary PDF, not a server-folder document.";
  }
  if (!fileCount) return activeFolder ? "Add PDFs to this folder first." : "Add at least one file.";
  if (activeFolder && fileCount > 20) return "Process no more than 20 PDFs at a time.";
  if (activeFolder && activeOperation === "images_to_pdf") {
    return "Images to PDF uses temporary image uploads, not server folders.";
  }
  if (!activeFolder) {
    const isImageOperation = activeOperation === "images_to_pdf";
    const isConversion = activeOperation === "convert";
    const sourceFormat = document.querySelector("#field-source_format")?.value || "pdf";
    const targetFormat = document.querySelector("#field-target_format")?.value || "";
    if (isConversion && !availableConversionTargets(sourceFormat).includes(targetFormat)) {
      return "Choose a supported source and target format.";
    }
    const invalidFile = selectedFiles.some((file) => {
      if (isConversion) return !conversionFileMatches(file, sourceFormat);
      if (isImageOperation) {
        return ![".png", ".jpg", ".jpeg"].some((extension) =>
          file.name.toLowerCase().endsWith(extension),
        );
      }
      return file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf");
    });
    if (invalidFile) {
      return isConversion
        ? `Every file must match the selected ${sourceFormat.toUpperCase()} source format.`
        : isImageOperation
        ? "Images to PDF accepts only PNG and JPEG files."
        : "This tool accepts only PDF files. Remove the image files first.";
    }
  }
  if (activeOperation === "merge" && fileCount < 2) {
    return "Add at least two PDFs to merge.";
  }
  if (activeOperation === "overlay" && fileCount !== 2) {
    return "Choose exactly two PDFs: the base PDF first and the overlay second.";
  }
  if (activeOperation === "split") {
    const mode = document.querySelector("#field-split_mode")?.value || "pages";
    if (mode === "document_count" && fileCount < 2) {
      return "Add at least two PDFs for document-count splitting.";
    }
    if (mode !== "document_count" && fileCount !== 1) {
      return "Choose exactly one PDF for this split method.";
    }
    if (["pages", "file_size", "page_count", "document_count", "page_divider"].includes(mode)) {
      const fieldName = mode === "pages" ? "groups" : mode === "page_divider" ? "divider_text" : "split_value";
      if (!document.querySelector(`#field-${fieldName}`)?.value.trim()) {
        return "Complete the selected split-method settings.";
      }
    }
  }
  if (activeOperation === "crop") {
    if (fileCount !== 1) return "Choose exactly one PDF to crop.";
    if (
      !["crop_x", "crop_y", "crop_width", "crop_height"].every(
        (name) => document.querySelector(`#field-${name}`)?.value,
      )
    ) {
      return "Select a crop area in the preview first.";
    }
  }
  if (activeOperation === "stamp") {
    const mode = document.querySelector("#field-stamp_mode")?.value || "text";
    if (
      ["text", "both"].includes(mode) &&
      !document.querySelector("#field-stamp_text")?.value.trim()
    ) {
      return "Enter the text to use on the stamp.";
    }
    if (
      ["image", "both"].includes(mode) &&
      !document.querySelector("#field-stamp_image")?.files.length
    ) {
      return "Choose a PNG or JPEG stamp image.";
    }
  }
  if (
    !activeFolder &&
    [
      "pdf_to_images",
      "protect",
      "unlock",
      "remove_password",
      "personal_signature",
      "digital_signature",
      "crop",
      "stamp",
      "sanitize",
      "flatten",
      "unlock_forms",
      "change_permissions",
      "remove_pages",
      "remove_blank_pages",
      "remove_annotations",
      "remove_images",
      "remove_certificate_sign",
      "auto_form_detection",
      "auto_rename",
      "automate",
      "add_text",
      "add_image",
      "annotate",
      "form_editor",
      "adjust_colors",
      "repair",
      "split_scanned_photos",
      "color_transform",
    ].includes(activeOperation) &&
    fileCount !== 1
  ) {
    return "Choose exactly one PDF for this tool.";
  }
  if (activeOperation === "add_image" && !document.querySelector("#field-tool_image")?.files.length) {
    return "Choose a PNG or JPEG image to add.";
  }
  if (
    activeOperation === "automate" &&
    ![...(document.querySelector("#field-automation_steps")?.selectedOptions || [])].length
  ) {
    return "Select at least one automation step.";
  }
  if (activeOperation === "form_editor" && !document.querySelector("[data-form-field]")) {
    return "Load the PDF's interactive form fields before submitting.";
  }
  if (activeOperation === "personal_signature") {
    const savedSignatureId = document.querySelector("#saved-signature-id")?.value;
    if (savedSignatureId) return "";
    const mode = document.querySelector(".signature-tab.is-selected")?.dataset.signatureMode;
    const typedText = document.querySelector("#signature-text")?.value.trim();
    const upload = document.querySelector("#signature-upload")?.files.length;
    if (mode === "draw" && !signatureHasContent) return "Draw your signature in the preview first.";
    if (mode === "type" && !typedText) return "Enter the name to use as your signature.";
    if (mode === "upload" && !upload) return "Choose a PNG or JPEG signature image.";
    if (
      document.querySelector("#save-to-signatures")?.checked &&
      !document.querySelector("#signature-name")?.value.trim()
    ) {
      return "Enter a name for the saved signature.";
    }
  }
  if (activeOperation === "digital_signature" && !document.querySelector("#field-certificate_file")?.files.length) {
    return "Choose a .p12 or .pfx certificate.";
  }
  return "";
}

function isPdfFile(file) {
  const name = file.name.toLowerCase();
  return file.type === "application/pdf" || name.endsWith(".pdf");
}

function setFoldersStatus(message, isError = false) {
  const foldersStatus = document.querySelector("#folders-status");
  foldersStatus.textContent = message;
  foldersStatus.classList.toggle("error", isError);
}

function showRequestError(response, fallback) {
  return response.json().catch(() => ({})).then((body) => {
    throw new Error(body.detail || fallback);
  });
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function createFileItem(file, index, isStored) {
  const item = document.createElement("li");
  item.className = "file-item";
  const type = document.createElement("span");
  type.className = "file-type";
  type.textContent = file.name.split(".").pop().slice(0, 4).toUpperCase();
  const information = document.createElement("div");
  information.className = "file-info";
  const name = document.createElement(isStored ? "a" : "div");
  name.className = "file-name";
  name.title = file.name;
  name.textContent = file.name;
  if (isStored) {
    name.href = `/api/folders/${activeFolder.id}/documents/${file.id}`;
    name.title = `Review ${file.name} before downloading`;
    name.addEventListener("click", async (event) => {
      event.preventDefault();
      if (pendingResult) {
        setStatus("Download or undo the reviewed result before opening another file.", true);
        return;
      }
      try {
        const response = await fetch(name.href);
        if (!response.ok) {
          await showRequestError(response, "Could not open this stored PDF.");
        }
        await stageResult(
          await response.blob(),
          file.name,
          response.headers.get("content-type"),
        );
      } catch (error) {
        setStatus(error.message || "Could not open this stored PDF.", true);
      }
    });
  }
  const size = document.createElement("div");
  size.className = "file-size";
  size.textContent = formatFileSize(isStored ? file.size_bytes : file.size);
  information.append(name, size);
  const remove = document.createElement("button");
  remove.className = "remove-file";
  remove.type = "button";
  remove.setAttribute("aria-label", `Remove ${file.name}`);
  if (isStored) remove.dataset.removeStoredDocument = String(file.id);
  else remove.dataset.removeFile = String(index);
  remove.textContent = "×";
  item.append(type, information, remove);
  return item;
}

function renderFiles() {
  const libraryList = document.querySelector("#library-list");
  const visibleFiles = activeFolder ? folderDocuments : selectedFiles;
  const emptyLabel = document.querySelector("#library-empty");
  libraryList.replaceChildren();
  visibleFiles.forEach((file, index) =>
    libraryList.append(createFileItem(file, index, Boolean(activeFolder))),
  );
  const hasFiles = visibleFiles.length > 0;
  emptyLabel.hidden = hasFiles;
  emptyLabel.innerHTML = activeFolder
    ? "This server folder is empty.<br>Add PDFs to get started."
    : "No PDFs yet.<br>Add files to get started.";
  libraryList.hidden = !hasFiles;
  document.querySelector("#canvas-empty").hidden = hasFiles;
  document.querySelector("#canvas-ready").hidden = !hasFiles;
  document.querySelector("#clear-files-button").hidden =
    Boolean(activeFolder) || selectedFiles.length === 0;
  document.querySelector("#clear-folder-button").hidden = !activeFolder;
  document.querySelector("#active-folder-label").hidden = !activeFolder;
  document.querySelector("#active-folder-label").textContent = activeFolder
    ? `Server folder: ${activeFolder.name}`
    : "";
  document.querySelector("#pane-description").textContent = activeFolder
    ? "Encrypted server folder"
    : "Temporary files in this session";
  document.querySelector("#selected-count").textContent = activeFolder
    ? `${visibleFiles.length} PDF${visibleFiles.length === 1 ? "" : "s"} in folder`
    : `${visibleFiles.length} file${visibleFiles.length === 1 ? "" : "s"} ready`;
  document.querySelector("#canvas-empty").querySelector("p").textContent = activeFolder
    ? "Add PDFs to this encrypted folder, then choose a tool to process them."
    : "Add one or more PDFs to start using your selected tool.";
  document.querySelector("#canvas-ready").querySelector("p").textContent = activeFolder
    ? "Stored PDFs remain encrypted in this server folder."
    : "Your selected files are listed in the library.";
  submitButton.textContent = activeFolder
    ? `Process folder: ${toolDetails[activeOperation].title}`
    : activeOperation === "personal_signature"
      ? "Use signature"
      : `Run ${toolDetails[activeOperation].title}`;
  document.querySelector("#add-files-button").title = activeFolder
    ? "Add PDFs to this server folder"
    : "Add PDFs";
  document.querySelector("#stage-add-files").textContent = activeFolder
    ? "Add PDFs to folder"
    : "＋ Add PDFs";
  document.querySelector("#add-more-files").textContent = activeFolder
    ? "Add PDFs to folder"
    : "Add more PDFs";
  if (activeOperation === "crop") renderFields();
  updateRunButton();
}

async function loadFolderDocuments(folderId) {
  const response = await fetch(`/api/folders/${folderId}/documents`);
  if (!response.ok) return showRequestError(response, "Could not load folder documents.");
  const documents = await response.json();
  if (activeFolder && activeFolder.id === folderId) {
    folderDocuments = documents;
    renderFiles();
  }
  return documents;
}

async function loadFolders() {
  const response = await fetch("/api/folders");
  if (!response.ok) return showRequestError(response, "Could not load server folders.");
  const folders = await response.json();
  const list = document.querySelector("#folders-list");
  list.replaceChildren();
  if (!folders.length) {
    const empty = document.createElement("li");
    empty.className = "folders-empty";
    empty.textContent = "No server folders yet. Create one to store PDFs.";
    list.append(empty);
    return;
  }
  for (const folder of folders) {
    const item = document.createElement("li");
    item.className = "folder-row";
    const info = document.createElement("div");
    info.className = "folder-row-info";
    const name = document.createElement("div");
    name.className = "folder-row-name";
    name.textContent = folder.name;
    const count = document.createElement("div");
    count.className = "folder-row-count";
    count.textContent = `${folder.document_count} PDF${folder.document_count === 1 ? "" : "s"}`;
    info.append(name, count);
    const open = document.createElement("button");
    open.className = "button button-secondary";
    open.type = "button";
    open.dataset.openFolder = String(folder.id);
    open.textContent = "Open";
    const remove = document.createElement("button");
    remove.className = "button button-quiet";
    remove.type = "button";
    remove.dataset.deleteFolder = String(folder.id);
    remove.textContent = "Delete";
    item.append(info, open, remove);
    list.append(item);
  }
}

async function selectFolder(folder) {
  try {
    const response = await fetch(`/api/folders/${folder.id}/documents`);
    if (!response.ok) return await showRequestError(response, "Could not load folder documents.");
    folderDocuments = await response.json();
    activeFolder = folder;
    foldersDialog.close();
    fileInput.accept = "application/pdf,.pdf";
    document.querySelector("#file-guidance").textContent =
      "PDF files added here are encrypted and saved in this server folder.";
    renderFiles();
    const validationError = fileValidationError();
    setStatus(
      validationError || `Using encrypted server folder: ${folder.name}.`,
      Boolean(validationError),
    );
  } catch (error) {
    setFoldersStatus(error.message || "Could not open this folder.", true);
  }
}

async function addFiles(incomingFiles) {
  const files = [...incomingFiles];
  if (activeFolder) {
    const folderId = activeFolder.id;
    const accepted = files.filter(isPdfFile);
    if (accepted.length !== files.length) setStatus("Server folders accept PDF files only.", true);
    if (!accepted.length) return;
    const data = new FormData();
    accepted.forEach((file) => data.append("files", file));
    try {
      const response = await fetch(`/api/folders/${folderId}/documents`, {
        method: "POST",
        body: data,
      });
      if (!response.ok) return await showRequestError(response, "Could not save PDFs to the folder.");
      await loadFolderDocuments(folderId);
      if (activeFolder && activeFolder.id === folderId) {
        setStatus(`${accepted.length} PDF${accepted.length === 1 ? "" : "s"} encrypted and saved.`);
      }
      await loadFolders();
    } catch (error) {
      setStatus(error.message || "Could not save PDFs to the folder.", true);
    }
    return;
  }
  const imageMode = activeOperation === "images_to_pdf";
  const conversionMode = activeOperation === "convert";
  const sourceFormat = document.querySelector("#field-source_format")?.value || "pdf";
  const accepted = files.filter((file) => {
    return conversionMode
      ? conversionFileMatches(file, sourceFormat)
      : imageMode
      ? [".png", ".jpg", ".jpeg"].some((extension) =>
          file.name.toLowerCase().endsWith(extension),
        )
      : isPdfFile(file);
  });
  if (accepted.length !== files.length) {
    setStatus(
      conversionMode
        ? `Choose only ${sourceFormat.toUpperCase()} files.`
        : imageMode
          ? "Choose PNG or JPEG images."
          : "Choose PDF files.",
      true,
    );
  }
  for (const file of accepted) {
    const alreadyAdded = selectedFiles.some(
      (existing) =>
        existing.name === file.name &&
        existing.size === file.size &&
        existing.lastModified === file.lastModified,
    );
    if (!alreadyAdded) selectedFiles.push(file);
  }
  if (selectedFiles.length > 20) {
    selectedFiles.splice(20);
    setStatus("Select no more than 20 files at a time.", true);
  } else {
    const validationError = fileValidationError();
    if (validationError) setStatus(validationError, true);
    else if (accepted.length === files.length) setStatus("");
  }
  renderFiles();
}

function selectTool(operation) {
  if (!toolDetails[operation]) return;
  if (!canUseOperation(operation)) {
    setStatus("Your account does not have the capability required for this tool.", true);
    return;
  }
  activeOperation = operation;
  for (const button of toolButtons) {
    const selected = button.dataset.operation === operation;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  document.querySelector("#active-tool-title").textContent = toolDetails[operation].title;
  document.querySelector("#active-tool-description").textContent =
    toolDetails[operation].description;
  submitButton.textContent =
    operation === "personal_signature" ? "Use signature" : `Run ${toolDetails[operation].title}`;
  renderFields();
  updateFileAcceptance();
  if (activeFolder) {
    document.querySelector("#file-guidance").textContent =
      "PDF files added here are encrypted and saved in this server folder.";
  }
  const validationError = activeFolder || selectedFiles.length ? fileValidationError() : "";
  setStatus(validationError, Boolean(validationError));
  updateRunButton();
}

function canUseOperation(operation) {
  if (!signedInUser) return true;
  if (
    !signedInUser.vision_enabled &&
    ["auto_form_detection", "split_scanned_photos"].includes(operation)
  ) {
    return false;
  }
  return (
    (signedInUser.can_edit && signedInUser.edit_operations.includes(operation)) ||
    (signedInUser.can_process && signedInUser.process_operations.includes(operation))
  );
}

function applyOperationCapabilities() {
  if (!signedInUser) return;
  const availableButtons = toolButtons.filter((button) =>
    canUseOperation(button.dataset.operation),
  );
  if (!canUseOperation(activeOperation) && availableButtons.length) {
    selectTool(availableButtons[0].dataset.operation);
  }
  filterTools(document.querySelector("#tool-search").value);
}

function filterTools(query) {
  const normalizedQuery = query.trim().toLowerCase();
  for (const category of document.querySelectorAll(".tool-category")) {
    const buttons = [...category.querySelectorAll(".tool-button")];
    let visibleCount = 0;
    for (const button of buttons) {
      const visible =
        canUseOperation(button.dataset.operation) &&
        button.textContent.toLowerCase().includes(normalizedQuery);
      button.hidden = !visible;
      if (visible) visibleCount += 1;
    }
    category.hidden = visibleCount === 0;
  }
  document.querySelector("#no-tools-found").hidden =
    [...document.querySelectorAll(".tool-category")].some((category) => !category.hidden);
}

function renderJobs(jobs) {
  const list = document.querySelector("#jobs-list");
  list.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "Completed jobs will appear here.";
    list.append(empty);
    return;
  }
  for (const job of jobs) {
    const item = document.createElement("li");
    item.className = "job";
    const name = document.createElement("span");
    name.className = "job-name";
    name.textContent = job.operation;
    const time = document.createElement("span");
    time.className = "job-time";
    time.textContent = new Date(job.created_at).toLocaleString();
    item.append(name, time);
    list.append(item);
  }
}

async function loadAccount() {
  const response = await fetch("/api/me");
  if (response.status === 401 || response.status === 403) {
    let message = "";
    if (response.status === 403) {
      const body = await response.json().catch(() => ({}));
      message = body.detail || "Your account cannot access this workspace.";
    }
    const optionsResponse = await fetch("/api/auth/options");
    if (!optionsResponse.ok) throw new Error("Could not load sign-in options.");
    const options = await optionsResponse.json();
    document.querySelector("#entra-login").hidden = !options.entra_enabled;
    document.querySelector("#login-divider").hidden = !options.entra_enabled;
    document.querySelector("#local-admin-config-note").hidden = options.local_admin_enabled;
    document.querySelector("#signed-out").hidden = false;
    document.querySelector("#login-error").textContent = message;
    return;
  }
  if (!response.ok) throw new Error("Could not load your account.");
  const user = await response.json();
  signedInUser = user;
  document.querySelector("#workspace").hidden = false;
  document.querySelector("#theme-picker-button").hidden = true;
  document.querySelector("#account").hidden = false;
  document.querySelector("#user-name").textContent = user.name || user.email || "Account";
  document.querySelector("#user-avatar").textContent =
    (user.name || user.email || "P").trim().charAt(0).toUpperCase() || "P";
  document.querySelector("#account-menu-name").textContent =
    user.name || user.email || "PDF Workplace user";
  document.querySelector("#account-menu-email").textContent =
    `${user.email || "No email"} · ${user.role.replaceAll("_", " ")}`;
  document.querySelector("#admin-navigation").hidden =
    !["org_owner", "admin"].includes(user.role);
  document.querySelector("#account-menu-users").hidden =
    !["org_owner", "admin"].includes(user.role);
  document.querySelector("#account-menu-server").hidden =
    !["org_owner", "admin"].includes(user.role);
  document.querySelector('#invitation-role option[value="admin"]').hidden =
    user.role !== "org_owner";
  document.querySelector('#invitation-role option[value="org_owner"]').hidden =
    user.role !== "org_owner";
  applyOperationCapabilities();
  updateRunButton();
  const jobsResponse = await fetch("/api/jobs");
  if (!jobsResponse.ok) throw new Error("Could not load recent activity.");
  renderJobs(await jobsResponse.json());
}

function closeAccountMenu() {
  document.querySelector("#account-menu").hidden = true;
  document.querySelector("#account-menu-button").setAttribute("aria-expanded", "false");
}

document.querySelector("#account-menu-button").addEventListener("click", () => {
  const menu = document.querySelector("#account-menu");
  menu.hidden = !menu.hidden;
  document.querySelector("#account-menu-button").setAttribute(
    "aria-expanded",
    String(!menu.hidden),
  );
});
document.querySelector("#account-menu").addEventListener("click", (event) => {
  const action = event.target.closest("[data-account-action]")?.dataset.accountAction;
  if (!action) return;
  closeAccountMenu();
  if (action === "settings") openThemeDialog();
  if (action === "users") setWorkspaceView("admin");
  if (action === "server-settings") setWorkspaceView("server-settings");
  if (action === "usage") setWorkspaceView("usage");
});
document.addEventListener("click", (event) => {
  if (!document.querySelector("#account").contains(event.target)) closeAccountMenu();
});

toolButtons.forEach((button) => {
  button.addEventListener("click", () => selectTool(button.dataset.operation));
});

document.querySelector("#manage-folders-button").addEventListener("click", async () => {
  foldersDialog.showModal();
  setFoldersStatus("Loading folders…");
  try {
    await loadFolders();
    setFoldersStatus("");
  } catch (error) {
    setFoldersStatus(error.message || "Could not load server folders.", true);
  }
});

document.querySelector("#close-folders-button").addEventListener("click", () => foldersDialog.close());
document.querySelector("#done-folders-button").addEventListener("click", () => foldersDialog.close());

document.querySelector("#folder-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const nameInput = document.querySelector("#folder-name");
  const name = nameInput.value.trim();
  try {
    const response = await fetch("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!response.ok) return await showRequestError(response, "Could not create the folder.");
    const folder = await response.json();
    nameInput.value = "";
    await loadFolders();
    await selectFolder(folder);
    setFoldersStatus("");
  } catch (error) {
    setFoldersStatus(error.message || "Could not create the folder.", true);
  }
});

document.querySelector("#folders-list").addEventListener("click", async (event) => {
  const openButton = event.target.closest("[data-open-folder]");
  const deleteButton = event.target.closest("[data-delete-folder]");
  if (openButton) {
    const folder = { id: Number(openButton.dataset.openFolder), name: openButton.closest(".folder-row").querySelector(".folder-row-name").textContent };
    await selectFolder(folder);
    return;
  }
  if (deleteButton) {
    const folderId = Number(deleteButton.dataset.deleteFolder);
    if (!window.confirm("Delete this folder and all of its stored PDFs?")) return;
    try {
      const response = await fetch(`/api/folders/${folderId}`, { method: "DELETE" });
      if (!response.ok) return await showRequestError(response, "Could not delete the folder.");
      if (activeFolder && activeFolder.id === folderId) {
        activeFolder = null;
        folderDocuments = [];
        updateFileAcceptance();
        renderFiles();
      }
      await loadFolders();
      setFoldersStatus("Folder deleted.");
    } catch (error) {
      setFoldersStatus(error.message || "Could not delete the folder.", true);
    }
  }
});

for (const buttonId of ["add-files-button", "stage-add-files", "add-more-files"]) {
  document.querySelector(`#${buttonId}`).addEventListener("click", () => fileInput.click());
}

fileInput.addEventListener("change", () => {
  void addFiles(fileInput.files);
  fileInput.value = "";
});

document.querySelector("#clear-files-button").addEventListener("click", () => {
  selectedFiles.length = 0;
  setStatus("");
  renderFiles();
});

document.querySelector("#library-list").addEventListener("click", (event) => {
  const removeButton = event.target.closest("[data-remove-file]");
  const removeStoredButton = event.target.closest("[data-remove-stored-document]");
  if (removeButton) {
    selectedFiles.splice(Number(removeButton.dataset.removeFile), 1);
    renderFiles();
  }
  if (removeStoredButton && activeFolder) {
    const folderId = activeFolder.id;
    const id = Number(removeStoredButton.dataset.removeStoredDocument);
    fetch(`/api/folders/${folderId}/documents/${id}`, { method: "DELETE" })
      .then((response) =>
        response.ok ? loadFolderDocuments(folderId) : showRequestError(response, "Could not remove the PDF."),
      )
      .then(() => loadFolders())
      .catch((error) => setStatus(error.message || "Could not remove the PDF.", true));
  }
});

document.querySelector("#clear-folder-button").addEventListener("click", () => {
  activeFolder = null;
  folderDocuments = [];
  updateFileAcceptance();
  setStatus("");
  renderFiles();
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("is-dragging");
});
dropZone.addEventListener("dragleave", (event) => {
  if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove("is-dragging");
});
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("is-dragging");
  void addFiles(event.dataTransfer.files);
});

document.querySelector("#tool-search").addEventListener("input", (event) => {
  filterTools(event.target.value);
});
document.querySelector("#show-javascript").addEventListener("click", async () => {
  const dialog = document.querySelector("#developer-tools-dialog");
  const content = document.querySelector("#developer-tools-dialog-content");
  document.querySelector("#developer-tools-dialog-title").textContent = "Show Javascript";
  content.replaceChildren();
  const message = document.createElement("p");
  message.className = "modal-description";
  message.textContent = "Loading the frontend source…";
  content.append(message);
  dialog.showModal();
  try {
    const response = await fetch("/app.js", { cache: "no-store" });
    if (!response.ok) throw new Error(`Could not load JavaScript source (HTTP ${response.status}).`);
    const source = await response.text();
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = source;
    pre.append(code);
    content.replaceChildren(pre);
  } catch (error) {
    message.textContent = error.message || "Could not load JavaScript source.";
  }
});
document.querySelectorAll("[data-developer-guide]").forEach((button) => {
  button.addEventListener("click", () => {
    const guide = developerGuides[button.dataset.developerGuide];
    if (guide) showDeveloperGuide(guide.title, guide.paragraphs);
  });
});
document.querySelector("#theme-picker-button").addEventListener("click", openThemeDialog);
document.querySelector("#rail-theme-picker").addEventListener("click", openThemeDialog);
document.querySelectorAll('input[name="color-theme"]').forEach((input) => {
  input.addEventListener("change", () => {
    applyTheme(input.value);
    try {
      localStorage.setItem("pdf-workplace-theme", input.value);
      document.querySelector("#theme-status").textContent =
        `${themeNames[input.value]} theme saved.`;
    } catch (error) {
      console.error("Could not save the selected color theme.", error);
      document.querySelector("#theme-status").textContent =
        "Theme changed, but this browser could not save the preference.";
    }
  });
});
document.querySelector("#close-theme-dialog").addEventListener("click", () => {
  document.querySelector("#theme-dialog").close();
});
document.querySelector("#done-theme-dialog").addEventListener("click", () => {
  document.querySelector("#theme-dialog").close();
});
document.querySelector("#download-result").addEventListener("click", downloadPendingResult);
document.querySelector("#undo-result").addEventListener("click", () => {
  clearPendingResult();
  setStatus("The generated result was discarded. Your original PDF is unchanged.");
});
document.querySelector("#close-developer-tools").addEventListener("click", () => {
  document.querySelector("#developer-tools-dialog").close();
});
document.querySelector("#done-developer-tools").addEventListener("click", () => {
  document.querySelector("#developer-tools-dialog").close();
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    document.querySelector("#tool-search").focus();
  }
  if (event.key === "Escape") closeAccountMenu();
});

document.querySelectorAll(".rail-button").forEach((button) => {
  button.addEventListener("click", () => {
    setWorkspaceView(button.dataset.destination);
    if (button.dataset.destination === "files") fileInput.click();
    if (button.dataset.destination === "activity") {
      document.querySelector("#activity-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });
});

document.querySelector("#new-team-button").addEventListener("click", () => {
  document.querySelector("#team-create-form").reset();
  document.querySelector("#team-dialog").showModal();
});
document.querySelector("#close-team-dialog").addEventListener("click", () => {
  document.querySelector("#team-dialog").close();
});
document.querySelector("#team-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const response = await fetch("/api/admin/teams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: document.querySelector("#team-name").value }),
    });
    if (!response.ok) await showRequestError(response, "Could not create the team.");
    document.querySelector("#team-dialog").close();
  } catch (error) {
    setAdminStatus(error.message || "Could not create the team.", true);
    return;
  } finally {
    submit.disabled = false;
  }
  try {
    await loadAdminManagement();
    setAdminStatus("Team created.");
  } catch (error) {
    setAdminStatus(`Team created, but the list could not be refreshed: ${error.message}`, true);
  }
});

document.querySelector("#invite-people-button").addEventListener("click", () => {
  document.querySelector("#invitation-form").reset();
  document.querySelector("#invitation-status").textContent = "";
  document.querySelector("#invitation-dialog").showModal();
});
document.querySelector("#close-invitation-dialog").addEventListener("click", () => {
  document.querySelector("#invitation-dialog").close();
});
document.querySelector("#invitation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  const emails = document.querySelector("#invitation-emails").value
    .split(/\r?\n/)
    .map((email) => email.trim())
    .filter(Boolean);
  submit.disabled = true;
  document.querySelector("#invitation-status").textContent = "Creating invitations…";
  let result;
  try {
    const response = await fetch("/api/admin/invitations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        emails,
        team_id: Number(document.querySelector("#invitation-team").value),
        role: document.querySelector("#invitation-role").value,
        can_edit: document.querySelector("#invitation-can-edit").checked,
        can_process: document.querySelector("#invitation-can-process").checked,
      }),
    });
    if (!response.ok) await showRequestError(response, "Could not create invitations.");
    result = await response.json();
    document.querySelector("#invitation-dialog").close();
  } catch (error) {
    document.querySelector("#invitation-status").textContent =
      error.message || "Could not create invitations.";
    return;
  } finally {
    submit.disabled = false;
  }
  try {
    await loadAdminManagement();
    setAdminStatus(
      `${result.created} invitation${result.created === 1 ? "" : "s"} created. No email was sent; users must sign in through Entra.`,
    );
  } catch (error) {
    setAdminStatus(
      `${result.created} invitations created, but the list could not be refreshed: ${error.message}`,
      true,
    );
  }
});

document.querySelector("#admin-team-filters").addEventListener("click", (event) => {
  const filter = event.target.closest("[data-team-filter]");
  if (!filter) return;
  adminTeamFilter = filter.dataset.teamFilter ? Number(filter.dataset.teamFilter) : null;
  adminOffset = 0;
  loadAdminTeams()
    .then(() => loadAdminUsers())
    .catch((error) => setAdminStatus(error.message || "Could not filter users.", true));
});

document.querySelector("#admin-search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  adminOffset = 0;
  loadAdminUsers().catch((error) =>
    setAdminStatus(error.message || "Could not load users.", true),
  );
});

document.querySelector("#admin-previous-page").addEventListener("click", () => {
  adminOffset = Math.max(0, adminOffset - adminPageSize);
  loadAdminUsers().catch((error) =>
    setAdminStatus(error.message || "Could not load users.", true),
  );
});

document.querySelector("#admin-next-page").addEventListener("click", () => {
  if (adminOffset + adminPageSize >= adminTotal) return;
  adminOffset += adminPageSize;
  loadAdminUsers().catch((error) =>
    setAdminStatus(error.message || "Could not load users.", true),
  );
});

document.querySelector("#admin-users-list").addEventListener("change", (event) => {
  const row = event.target.closest("tr");
  if (!row) return;
  const userId = Number(row.dataset.userId);
  const roleSelect = event.target.closest(".admin-role-select");
  const teamSelect = event.target.closest(".admin-team-select");
  const capability = event.target.closest("[data-user-capability]");
  const changes = roleSelect
    ? { role: roleSelect.value }
    : teamSelect
      ? { team_id: Number(teamSelect.value) }
      : capability
        ? { [capability.dataset.userCapability]: capability.checked }
        : null;
  if (!changes) return;
  updateAdminUser(userId, changes).catch((error) =>
    setAdminStatus(error.message || "Could not update this user's access.", true),
  );
});

document.querySelector("#admin-users-list").addEventListener("click", (event) => {
  const toggleButton = event.target.closest("[data-toggle-user]");
  if (!toggleButton) return;
  const row = toggleButton.closest("tr");
  const userId = Number(row.dataset.userId);
  const isActive = row.dataset.userActive === "true";
  updateAdminUser(userId, { is_active: !isActive }).catch((error) =>
    setAdminStatus(error.message || "Could not update this user's access.", true),
  );
});

document.querySelector("#admin-invitations-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-revoke-invitation]");
  if (!button) return;
  button.disabled = true;
  let revoked = false;
  try {
    const response = await fetch(`/api/admin/invitations/${button.dataset.revokeInvitation}`, {
      method: "DELETE",
    });
    if (!response.ok) await showRequestError(response, "Could not revoke the invitation.");
    revoked = true;
  } catch (error) {
    setAdminStatus(error.message || "Could not revoke the invitation.", true);
  } finally {
    button.disabled = false;
  }
  if (!revoked) return;
  try {
    await loadAdminManagement();
    setAdminStatus("Invitation revoked.");
  } catch (error) {
    setAdminStatus(`Invitation revoked, but the list could not be refreshed: ${error.message}`, true);
  }
});

document.querySelector("#local-admin-login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.querySelector("#local-login-error");
  error.textContent = "";
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const response = await fetch("/auth/local-login", {
      method: "POST",
      body: new FormData(event.currentTarget),
    });
    if (!response.ok) await showRequestError(response, "Local administrator sign-in failed.");
    window.location.assign("/");
  } catch (requestError) {
    error.textContent = requestError.message || "Local administrator sign-in failed.";
  } finally {
    submit.disabled = false;
  }
});

document.querySelector("#toggle-password-visibility").addEventListener("click", (event) => {
  const button = event.currentTarget;
  const password = document.querySelector("#login-password");
  const showPassword = password.type === "password";
  password.type = showPassword ? "text" : "password";
  button.setAttribute("aria-label", showPassword ? "Hide password" : "Show password");
  button.setAttribute("aria-pressed", String(showPassword));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const validationError = fileValidationError();
  if (validationError) return setStatus(validationError, true);
  if (activeOperation === "personal_signature") {
    const savedSignatureId = document.querySelector("#saved-signature-id").value;
    document.querySelector("#signature-image-data").value = savedSignatureId
      ? ""
      : document.querySelector("#signature-canvas").toDataURL("image/png");
  }
  const savesSignature =
    activeOperation === "personal_signature" &&
    document.querySelector("#save-to-signatures")?.checked;
  const data = new FormData(form);
  if (!activeFolder) {
    for (const file of selectedFiles) data.append("files", file);
  }
  if (activeOperation === "form_editor") {
    const formValues = {};
    for (const input of form.querySelectorAll("[data-form-field]")) {
      formValues[input.dataset.formField] =
        input.type === "checkbox" ? input.checked : input.value;
    }
    data.set("form_values", JSON.stringify(formValues));
  }
  if (activeOperation === "automate") {
    data.set(
      "automation_steps",
      JSON.stringify(
        [...document.querySelector("#field-automation_steps").selectedOptions].map(
          (option) => option.value,
        ),
      ),
    );
  }
  if (["protect", "change_permissions"].includes(activeOperation)) {
    const allowedActions = [...form.querySelectorAll("[data-permission]:checked")].map(
      (input) => input.dataset.permission,
    );
    data.set("permission_actions", allowedActions.join(","));
  }
  submitButton.disabled = true;
  setStatus(activeFolder ? "Processing the server folder…" : "Working on your PDF…");
  try {
    const signingEndpoints = {
      personal_signature: "/api/sign/personal",
      digital_signature: "/api/sign/digital",
    };
    const endpoint = activeFolder
      ? `/api/folders/${activeFolder.id}/process/${activeOperation}`
      : signingEndpoints[activeOperation] || `/api/process/${activeOperation}`;
    const response = await fetch(endpoint, { method: "POST", body: data });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "The file could not be processed.");
    }
    const blob = await response.blob();
    const savedSignatureId = response.headers.get("x-saved-signature-id");
    const disposition = response.headers.get("content-disposition") || "";
    const filename =
      disposition.match(/filename="([^"]+)"/)?.[1] ||
      (activeFolder ? "folder-results.zip" : "pdf-workplace-output.pdf");
    await stageResult(blob, filename, response.headers.get("content-type"));
    setStatus(
      savesSignature
        ? "Signature applied and saved. Review the result, then choose Download."
        : activeFolder
          ? "Folder processing is complete. Review the result, then choose Download."
          : "Done. Review the result, then choose Download.",
    );
    if (activeOperation === "personal_signature" && refreshSavedSignatures) {
      try {
        await refreshSavedSignatures(savedSignatureId);
      } catch (error) {
        setStatus(
          `Signature applied, but the saved-signature list could not be refreshed: ${error.message}`,
          true,
        );
      }
    }
    const jobsResponse = await fetch("/api/jobs");
    if (jobsResponse.ok) renderJobs(await jobsResponse.json());
  } catch (error) {
    setStatus(error.message || "Something went wrong.", true);
  } finally {
    updateRunButton();
  }
});

initializeTheme();
selectTool(activeOperation);
renderFiles();
loadAccount().catch((error) => {
  document.querySelector("#signed-out").hidden = false;
  document.querySelector("#login-error").textContent =
    error.message || "Could not connect to PDF Workplace.";
});
