import os, io, re
from typing import List, Dict, Optional, Tuple
from datetime import datetime

import streamlit as st
from supabase import create_client, Client

# Optional parsers
try:
    import pdfplumber
except Exception:
    pdfplumber = None
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

APP_TITLE = "Vendor Payment Inquiry Reader"
TABLE_NAME = "invoices"  # change if your table name differs

# Your table columns (as provided):
# Supplier_Name, Invoice_Date, Total_Invoice_Amount, Currency, Status,
# Supplier_Invoice_No, Comments, Supplier_Invoice_Date, file_url

# ---------- Heuristics & Regex ----------
INVOICE_REGEXES = [
    r"invoice\s*(?:#|no\.?|id\:?)\s*([A-Z0-9\-_\/]{4,})",
    r"\bINV[-_\/]?(\d{4,})\b",
    r"\b([A-Z]{2,5}\d{4,})\b",
]

PAYMENT_INTENT_KEYWORDS = [
    "payment status",
    "paid?",
    "payment when",
    "remittance",
    "remittance advice",
    "payment date",
    "has it been paid",
    "when will i get paid",
    "invoice status",
    "payment confirmation",
    "receipt confirmation",
    "remit",
]

EMAIL_REGEX = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def is_payment_inquiry(text: str) -> bool:
    low = text.lower()
    score = sum(1 for k in PAYMENT_INTENT_KEYWORDS if k in low)
    return ("invoice" in low and score >= 1) or score >= 2


def extract_invoice_ids(text: str) -> List[str]:
    found: List[str] = []
    low = text.lower()
    for rx in INVOICE_REGEXES:
        for m in re.finditer(rx, low, flags=re.I):
            val = m.group(1).upper().strip(".,;: )(")
            if len(val) >= 4 and val not in found:
                found.append(val)
    return found


def extract_emails(text: str) -> List[str]:
    return sorted(set(re.findall(EMAIL_REGEX, text)))


# ---------- Supabase helpers ----------
def load_supabase() -> Optional[Client]:
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def storage_list(sb: Client, bucket: str, prefix: str = "", include_one_subfolder: bool = True) -> List[str]:
    names: List[str] = []

    def _list_once(pfx: str):
        try:
            items = sb.storage.from_(bucket).list(pfx, {"limit": 1000, "offset": 0, "sortBy": {"column": "name", "order": "asc"}})
        except Exception as e:
            st.error(f"Storage list error for prefix '{pfx}': {e}")
            items = []
        return items or []

    top = _list_once(prefix)
    for it in top:
        is_file = bool(it.get("id")) or (isinstance(it.get("metadata"), dict) and "size" in it["metadata"])
        path = f"{prefix.rstrip('/')}/{it['name']}" if prefix else it["name"]
        if is_file:
            names.append(path)
        elif include_one_subfolder:
            sub = _list_once(path)
            for s in sub:
                is_file2 = bool(s.get("id")) or (isinstance(s.get("metadata"), dict) and "size" in s["metadata"])
                if is_file2:
                    names.append(f"{path}/{s['name']}")

    names = [n for n in names if n.lower().endswith((".pdf", ".html", ".htm"))]
    return names


def storage_download(sb: Client, bucket: str, path: str) -> bytes:
    return sb.storage.from_(bucket).download(path)


def lookup_invoices_by_supplier_invoice_no(sb: Client, ids: List[str]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    if not (sb and ids):
        return out
    for i in range(0, len(ids), 50):
        part = ids[i:i + 50]
        resp = sb.table(TABLE_NAME).select("*").in_("Supplier_Invoice_No", part).execute()
        for row in resp.data or []:
            key = str(row.get("Supplier_Invoice_No") or "").upper()
            out[key] = row
    return out


# ---------- Parsing helpers ----------
def read_pdf(file_bytes: bytes) -> str:
    if not pdfplumber:
        return ""
    buf = io.BytesIO(file_bytes)
    out = []
    with pdfplumber.open(buf) as pdf:
        for p in pdf.pages:
            out.append(p.extract_text() or "")
    return "\n".join(out)


def read_html(file_bytes: bytes) -> str:
    if not BeautifulSoup:
        return ""
    html = file_bytes.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)


# ---------- Drafting email ----------
def draft_email(vendor_name: str, vendor_email: Optional[str], inv_no: str, row: Optional[Dict]) -> Tuple[str, str]:
    name = vendor_name or "Team"
    subject = f"Re: Payment Inquiry – {inv_no}"

    if not row:
        body = (
            f"Hi {name},\n\n"
            f"Thanks for reaching out. We couldn't find invoice {inv_no} in our records. "
            f"Could you please confirm the invoice number, amount, and date, or attach the invoice copy?\n\n"
            f"Regards,\nAccounts Payable"
        )
        return subject, body

    status = (row.get("Status") or "").title()
    comments = row.get("Comments") or ""
    amount_text = row.get("Total_Invoice_Amount")
    currency = row.get("Currency") or "USD"
    inv_date = row.get("Invoice_Date") or row.get("Supplier_Invoice_Date")

    details = []
    if amount_text:
        details.append(f"Amount: {currency} {amount_text}")
    if inv_date:
        details.append(f"Invoice Date: {inv_date}")
    if comments:
        details.append(f"Notes: {comments}")

    details_txt = "\n".join(f"- {d}" for d in details) if details else ""

    body = (
        f"Hi {name},\n\n"
        f"Here’s the status for invoice {inv_no}: {status}.\n"
    )
    if details_txt:
        body += f"\n{details_txt}\n"

    if status == "Paid":
        body += "\nIf you haven't received the remittance advice, let us know and we'll resend."
    elif status in {"Queued", "Processing"}:
        body += "\nWe expect completion soon; we'll notify you once it posts."
    elif status == "On Hold":
        body += "\nThis is pending additional review. We'll reach out if we need anything further."
    elif status in {"Rejected", "Unpaid"}:
        body += "\nPlease review the details above and let us know if any corrections are needed."

    body += "\n\nRegards,\nAccounts Payable"
    return subject, body


# ---------- Streamlit App ----------
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="📧", layout="wide")
    st.title("📧 Vendor Payment Inquiry Reader")
    st.caption("Reads PDF/HTML from Supabase Storage, detects payment inquiries, extracts invoice numbers, "
               "checks Supabase status, and drafts reply emails.")

    with st.expander("⚙️ Configuration", expanded=True):
        left, right = st.columns(2)
        with left:
            bucket = st.text_input("Supabase bucket", value=st.secrets.get("BUCKET_NAME", "vendor-inquiries"))
            prefix = st.text_input("Folder/prefix (optional)", value=st.secrets.get("BUCKET_PREFIX", ""))
            include_sub = st.checkbox("Include one subfolder level", value=True)
        with right:
            sb = load_supabase()
            st.write("Supabase connected ✅" if sb else "Supabase not configured ❌")
            st.write(f"Table: **{TABLE_NAME}**")

    if not sb:
        st.stop()

    files = storage_list(sb, bucket, prefix, include_one_subfolder=include_sub)
    st.markdown(f"**Found {len(files)} files** in bucket `{bucket}` with prefix `{prefix or '(root)'}`.")
    if not files:
        st.info("No PDF/HTML files found here. Check bucket/prefix or Storage policies.")
        st.stop()

    selected = st.multiselect("Select files to process", files, default=files[:10])

    results = []
    if st.button("Process selected files"):
        for idx, path in enumerate(selected, start=1):
            st.subheader(f"{idx}/{len(selected)} • {path}")
            try:
                blob = storage_download(sb, bucket, path)
            except Exception as e:
                st.error(f"Download failed: {e}")
                continue

            ext = path.split(".")[-1].lower()
            if ext == "pdf":
                text = read_pdf(blob)
            else:
                text = read_html(blob)

            if not text:
                st.warning("Could not parse file (install pdfplumber/beautifulsoup4). Skipping.")
                continue

            if not is_payment_inquiry(text):
                st.info("This document does not look like a payment inquiry. Skipping.")
                continue

            invoice_ids = extract_invoice_ids(text)
            emails = extract_emails(text)
            vendor_email = emails[0] if emails else None

            st.write("Detected invoice IDs:", ", ".join(invoice_ids) if invoice_ids else "—")
            st.write("Detected vendor email:", vendor_email or "—")

            if not invoice_ids:
                subject, body = draft_email("Vendor", vendor_email, "(not provided)", None)
                st.code(f"Subject: {subject}\n\n{body}")
                results.append({
                    "file": path, "invoice_no": None, "status": "Unknown", "action": "Drafted – needs invoice number",
                    "timestamp": datetime.utcnow().isoformat()+"Z"
                })
                continue

            lookup = lookup_invoices_by_supplier_invoice_no(sb, invoice_ids)
            pairs = [(iid, lookup.get(iid)) for iid in invoice_ids]

            for inv_no, row in pairs:
                vendor_name = (row or {}).get("Supplier_Name") or (vendor_email.split("@")[0].title() if vendor_email else "Vendor")
                subject, body = draft_email(vendor_name, vendor_email, inv_no, row)
                st.code(f"Subject: {subject}\n\n{body}")
                results.append({
                    "file": path,
                    "invoice_no": inv_no,
                    "status": (row or {}).get("Status", "Not Found"),
                    "action": "Drafted",
                    "timestamp": datetime.utcnow().isoformat()+"Z"
                })

    st.divider()
    st.subheader("Run Log")
    if results:
        import pandas as pd
        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)
        st.download_button("Download CSV log",
                           data=df.to_csv(index=False).encode("utf-8"),
                           file_name="run_log.csv",
                           mime="text/csv")

    st.caption("Set SUPABASE_URL, SUPABASE_ANON_KEY, BUCKET_NAME (and optional BUCKET_PREFIX) in Streamlit secrets.")


if __name__ == "__main__":
    main()
