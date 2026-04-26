/**
 * Minimal RFC-4180-ish CSV parser.
 * Handles quoted fields containing commas, escaped quotes ("") and CRLF.
 * Returns an array of row objects keyed by the header row.
 */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let cur: string[] = [];
  let field = "";
  let inQuotes = false;
  const push = () => {
    cur.push(field);
    field = "";
  };
  const newline = () => {
    push();
    rows.push(cur);
    cur = [];
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
      continue;
    }
    if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      push();
    } else if (c === "\n") {
      newline();
    } else if (c === "\r") {
      if (text[i + 1] === "\n") i++;
      newline();
    } else {
      field += c;
    }
  }
  if (field.length > 0 || cur.length > 0) {
    push();
    rows.push(cur);
  }

  if (rows.length === 0) return [];
  const header = rows[0].map((h) => h.trim());
  const out: Record<string, string>[] = [];
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    if (row.length === 1 && row[0] === "") continue;
    const obj: Record<string, string> = {};
    for (let c = 0; c < header.length; c++) {
      obj[header[c]] = (row[c] ?? "").trim();
    }
    out.push(obj);
  }
  return out;
}

export function toNumber(s: string | undefined | null): number | null {
  if (s === undefined || s === null || s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}
